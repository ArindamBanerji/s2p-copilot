"""
CopilotFramework router — domain-agnostic endpoints.
Any copilot (SOC, S2P, fraud) exposes these endpoints.
Safe to copy to copilot-sdk.

Discipline: handlers here must import only from:
  app.framework.*
  app.services.gae_state (until learning_state extraction complete)
  app.db.*
  gae.*
  standard library
No app.domains.soc.* imports allowed.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from datetime import datetime
from pydantic import BaseModel

from app.db.neo4j import neo4j_client

router = APIRouter()


# ============================================================================
# Request/Response Models (framework endpoints only)
# ============================================================================

class ShadowToggleRequest(BaseModel):
    enabled: bool


class AnalystActionRequest(BaseModel):
    decision_id: str
    analyst_action: str


class CheckpointCreateRequest(BaseModel):
    reason: str = "manual"


class RollbackRequest(BaseModel):
    checkpoint_id: str


class _GraphQueryRequest(BaseModel):
    cypher: str


class FreezeRequest(BaseModel):
    initiated_by: str
    reason: str


class RollbackInterventionRequest(BaseModel):
    snapshot_id: str
    initiated_by: str
    reason: str
    preview: bool = False


class ThresholdRequest(BaseModel):
    category: str
    new_threshold: float
    initiated_by: str
    reason: str


# ============================================================================
# GET /api/soc/centroid-evolution — Centroid delta history from Decision nodes
# Used by Tab-2 Section A/B and Tab-4 Chart A.
# ============================================================================

@router.get("/soc/centroid-evolution")
async def get_centroid_evolution(
    n: int = Query(default=200, ge=1, le=1000),
    category: Optional[str] = Query(default=None),
):
    """
    Return centroid delta history from Decision nodes.
    Used by Tab-2 Section A/B and Tab-4 Chart A.
    Returns [] if no Decision nodes have centroid_delta_norm set yet.
    """
    try:
        rows = await neo4j_client.run_query(
            """
            MATCH (d:Decision)
            WHERE d.centroid_delta_norm IS NOT NULL
              AND d.centroid_delta_norm > 0
              AND ($category IS NULL OR d.category = $category)
            RETURN d.id AS id,
                   d.centroid_delta_norm AS centroid_delta_norm,
                   d.category AS category,
                   d.action AS action,
                   d.correct AS correct,
                   d.verified_at AS verified_at
            ORDER BY d.verified_at ASC
            LIMIT $n
            """,
            {"category": category, "n": n},
        )
        result = []
        for i, r in enumerate(rows):
            result.append({
                "decision_number": i + 1,
                "id": r.get("id"),
                "centroid_delta_norm": float(r.get("centroid_delta_norm") or 0.0),
                "category": r.get("category") or "unknown",
                "action": r.get("action") or "unknown",
                "correct": bool(r.get("correct")),
                "verified_at": str(r.get("verified_at") or ""),
            })
        print(f"[SOC] centroid-evolution: returned {len(result)} records (n={n}, category={category!r})")
        return result
    except Exception as exc:
        print(f"[SOC] centroid-evolution query failed: {exc}")
        return []


# ============================================================================
# GET /api/soc/convergence-calendar — Convergence Calendar (L-08)
# CLAIM-CONV-01: N_half = f(q̄, σ, kernel). V is NOT causal.
# ============================================================================

@router.get("/soc/convergence-calendar")
async def get_convergence_calendar():
    """
    Return per-factor convergence calendar with N_half predictions.

    Reads sigma_per_factor, q_bar, V, kernel, and decisions_per_factor from
    live deployment state when available. Falls back to documented defaults
    when state is not yet initialised.
    """
    from app.services.convergence_calendar import build_convergence_calendar, SOC_FACTORS

    # ── defaults (used when deployment state unavailable) ──────────────────
    DEFAULT_SIGMA  = 0.15
    DEFAULT_Q_BAR  = 0.75
    DEFAULT_V      = 200.0
    DEFAULT_KERNEL = "l2"

    sigma_per_factor    = {f: DEFAULT_SIGMA for f in SOC_FACTORS}
    q_bar               = DEFAULT_Q_BAR
    V                   = DEFAULT_V
    kernel              = DEFAULT_KERNEL
    decisions_per_factor = {f: 0 for f in SOC_FACTORS}

    # ── try to read live state ──────────────────────────────────────────────
    try:
        from app.services.gae_state import get_learning_state
        ls = get_learning_state()

        # Decision count per factor — query Neo4j decision nodes grouped by factor
        try:
            rows = await neo4j_client.run_query(
                """
                MATCH (d:Decision)
                WHERE d.primary_factor IS NOT NULL
                RETURN d.primary_factor AS factor, count(d) AS cnt
                """,
            )
            for row in rows:
                factor_name = str(row.get("factor", ""))
                if factor_name in decisions_per_factor:
                    decisions_per_factor[factor_name] = int(row.get("cnt", 0))
        except Exception as exc:
            print(f"[convergence-calendar] decisions query failed: {exc}")

        # Overall decision count as fallback for factors not tagged
        total = getattr(ls, "decision_count", 0)
        if total and all(v == 0 for v in decisions_per_factor.values()):
            # Distribute evenly across factors when primary_factor tagging absent
            per = total // len(SOC_FACTORS)
            decisions_per_factor = {f: per for f in SOC_FACTORS}

    except RuntimeError:
        pass  # not yet initialised — stay with defaults
    except Exception as exc:
        print(f"[convergence-calendar] state read failed: {exc}")

    return build_convergence_calendar(
        sigma_per_factor=sigma_per_factor,
        q_bar=q_bar,
        V=V,
        kernel=kernel,
        decisions_per_factor=decisions_per_factor,
    )


# ============================================================================
# GET /api/soc/ols-status — OLS Dashboard (L-09)
# ============================================================================

@router.get("/soc/ols-status")
async def get_ols_status_endpoint():
    """
    Return OLS (Override Lift Score) dashboard status.

    Uses GAE 0.7.18 OLSMonitor (CUSUM, plateau-snapshot baseline).
    ACM activates only for analysts with >= 20 overrides.

    Response
    --------
    {
        "status": "warming_up" | "monitoring" | "alarm",
        "baseline_ols": float | null,
        "current_ols": float | null,
        "delta_pct": float | null,
        "cusum": float,
        "alarm": bool,
        "baseline_frozen": bool,
        "qualified_analysts": int,
        "acm_active": bool,
        "message": str,
    }
    """
    from app.services.ols_status import get_ols_status

    ols_history: list = []
    analyst_overrides: dict = {}
    warm_start_active: bool = False

    try:
        # Read OLS history from Decision nodes (ols_score property)
        result = await neo4j_client.run_query(
            "MATCH (d:Decision) WHERE d.ols_score IS NOT NULL "
            "RETURN d.ols_score AS ols_score ORDER BY d.decision_number ASC",
            {},
        )
        ols_history = [float(r["ols_score"]) for r in result]
    except Exception as exc:
        print(f"[ols-status] ols_history query failed: {exc}")

    try:
        # Read override counts per analyst
        result = await neo4j_client.run_query(
            "MATCH (d:Decision) WHERE d.analyst_id IS NOT NULL AND d.was_override = true "
            "RETURN d.analyst_id AS analyst_id, count(*) AS cnt",
            {},
        )
        analyst_overrides = {r["analyst_id"]: int(r["cnt"]) for r in result}
    except Exception as exc:
        print(f"[ols-status] analyst_overrides query failed: {exc}")

    try:
        # Check warm_start flag from LearningState node if present
        result = await neo4j_client.run_query(
            "MATCH (ls:LearningState) RETURN ls.warm_start_active AS warm_start LIMIT 1",
            {},
        )
        if result:
            warm_start_active = bool(result[0].get("warm_start", False))
    except Exception as exc:
        print(f"[ols-status] warm_start query failed: {exc}")

    return get_ols_status(
        ols_history=ols_history,
        warm_start_active=warm_start_active,
        analyst_overrides=analyst_overrides,
    )


# ============================================================================
# GET /api/soc/flywheel-comparison — W2 Flywheel Demo Moment (Feature 3)
# ============================================================================

@router.get("/soc/flywheel-comparison")
async def get_flywheel_comparison(alert_id: str = "ALERT-001", category: str = "credential_access"):
    """
    Return W2 flywheel Day-1 vs current comparison for a given alert category.

    Suppressed when TRIGGERED_EVOLUTION edge count < 10 (cold-start guard).

    Response
    --------
    {
        "suppressed": bool,
        "reason": str (if suppressed),
        "category": str,
        "day_1_snapshot": {...},
        "current": {...},
        "delta": {"confidence_gain", "action_changed", "edge_count_gain", "interpretation"},
    }
    """
    from app.services.flywheel_comparison import build_flywheel_comparison

    try:
        # Count TRIGGERED_EVOLUTION edges for this category
        edge_result = await neo4j_client.run_query(
            "MATCH (d:Decision)-[:TRIGGERED_EVOLUTION]->(e:EvolutionEvent) "
            "WHERE d.category = $category "
            "RETURN count(e) AS edge_count",
            {"category": category},
        )
        edge_count = int(edge_result[0]["edge_count"]) if edge_result else 0

        if edge_count < 10:
            return build_flywheel_comparison(
                current_edges=edge_count,
                current_factor_4=0.40,
                current_confidence=0.71,
                current_action="investigate",
                current_provenance="",
                category=category,
            )

        # Read latest factor_4 and confidence from most recent Decision for category
        decision_result = await neo4j_client.run_query(
            "MATCH (d:Decision) WHERE d.category = $category "
            "RETURN d.factor_snapshot[3] AS factor_4, d.confidence AS confidence, "
            "d.action AS action ORDER BY d.decision_number DESC LIMIT 1",
            {"category": category},
        )
        if decision_result:
            factor_4 = float(decision_result[0].get("factor_4") or 0.40)
            confidence = float(decision_result[0].get("confidence") or 0.71)
            action = str(decision_result[0].get("action") or "investigate")
        else:
            factor_4, confidence, action = 0.40, 0.71, "investigate"

        provenance = f"{edge_count} verified decisions on {category}. Pattern history strong."

        return build_flywheel_comparison(
            current_edges=edge_count,
            current_factor_4=factor_4,
            current_confidence=confidence,
            current_action=action,
            current_provenance=provenance,
            category=category,
        )

    except Exception as exc:
        print(f"[flywheel-comparison] Neo4j error: {exc}")
        return {"suppressed": True, "reason": "data_unavailable"}


# ============================================================================
# GET /api/soc/iks-trend — IKS v2 trend (Chart A replacement)
# ============================================================================

@router.get("/soc/iks-trend")
async def get_iks_trend_endpoint():
    """
    Return IKS v2 score trend for Chart A.

    Currently returns the current score as a single trend point.
    Future: store periodic IKSSnapshot nodes for historical trend.

    Response
    --------
    {
        "trend": [{"decisions": int, "iks_v2": float, "timestamp": str}],
        "current": {"iks_v2": float, "components": dict, "interpretation": str},
    }
    """
    from app.services.iks import compute_iks_v2

    try:
        current = await compute_iks_v2(neo4j_client)
    except Exception as exc:
        print(f"[SOC] iks-trend compute failed: {exc}")
        current = {
            "iks_v2": 0.0,
            "components": {},
            "interpretation": "unavailable",
            "total_decisions": 0,
            "categories_active": 0,
        }

    trend_point = {
        "decisions":  current.get("total_decisions", 0),
        "iks_v2":     current.get("iks_v2", 0.0),
        "timestamp":  datetime.utcnow().isoformat() + "Z",
    }

    return {
        "trend": [trend_point],
        "current": {
            "iks_v2":         current.get("iks_v2", 0.0),
            "components":     current.get("components", {}),
            "interpretation": current.get("interpretation", ""),
        },
    }


# ============================================================================
# Shadow mode endpoints  (Phase 3)
# ============================================================================

@router.post("/soc/shadow/toggle")
async def shadow_toggle(request: ShadowToggleRequest):
    """Enable or disable shadow mode."""
    from app.services.shadow_mode import ShadowModeService
    ShadowModeService.SHADOW_ENABLED = request.enabled
    return {"shadow_mode": ShadowModeService.SHADOW_ENABLED}


@router.post("/soc/shadow/analyst-action")
async def shadow_analyst_action(request: AnalystActionRequest):
    """Record what the analyst actually did for a shadow decision."""
    from app.services.shadow_mode import ShadowModeService
    await ShadowModeService.record_analyst_action(
        decision_id=request.decision_id,
        analyst_action=request.analyst_action,
        neo4j_service=neo4j_client,
    )
    return {"recorded": True}


@router.get("/soc/shadow/report")
async def shadow_report():
    """Return shadow mode agreement report by category."""
    from app.services.shadow_mode import ShadowModeService
    return await ShadowModeService.get_shadow_report(neo4j_client)


# ============================================================================
# Checkpoint / Rollback endpoints  (Phase 4 §17.5)
# ============================================================================

@router.post("/soc/checkpoint/create")
async def checkpoint_create(request: CheckpointCreateRequest):
    """Snapshot current centroids to a Checkpoint node."""
    from app.services.checkpoint import CheckpointService
    from app.services.gae_state import get_profile_scorer
    try:
        scorer = get_profile_scorer()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Scorer not ready: {exc}")

    checkpoint_id = await CheckpointService.create_checkpoint(
        scorer=scorer,
        neo4j_service=neo4j_client,
        reason=request.reason,
    )
    return {
        "checkpoint_id": checkpoint_id,
        "timestamp":     datetime.utcnow().isoformat() + "Z",
        "reason":        request.reason,
    }


@router.get("/soc/checkpoint/list")
async def checkpoint_list():
    """List all checkpoints ordered by timestamp DESC."""
    from app.services.checkpoint import CheckpointService
    checkpoints = await CheckpointService.list_checkpoints(neo4j_client)
    return {"checkpoints": checkpoints}


@router.post("/soc/checkpoint/rollback")
async def checkpoint_rollback(request: RollbackRequest):
    """Restore centroids from a checkpoint and freeze the scorer."""
    from app.services.checkpoint import CheckpointService
    from app.services.gae_state import get_profile_scorer
    try:
        scorer = get_profile_scorer()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Scorer not ready: {exc}")

    result = await CheckpointService.rollback(
        checkpoint_id=request.checkpoint_id,
        scorer=scorer,
        neo4j_service=neo4j_client,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ============================================================================
# Scorer freeze / unfreeze  (Phase 4)
# ============================================================================

@router.post("/soc/scorer/freeze")
async def scorer_freeze():
    """Freeze the ProfileScorer — stops centroid updates."""
    from app.services.gae_state import get_profile_scorer
    try:
        scorer = get_profile_scorer()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Scorer not ready: {exc}")
    scorer.freeze()
    return {"frozen": True}


@router.post("/soc/scorer/unfreeze")
async def scorer_unfreeze():
    """Unfreeze the ProfileScorer — re-enables centroid updates."""
    from app.services.gae_state import get_profile_scorer
    try:
        scorer = get_profile_scorer()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Scorer not ready: {exc}")
    scorer.unfreeze()
    return {"frozen": False}


# ============================================================================
# GET /api/soc/auto-approve-stats  — Phase 5 coverage dashboard
# ============================================================================

@router.get("/soc/auto-approve-stats")
async def auto_approve_stats():
    """Return per-category auto-approve coverage.

    Response
    --------
    {
        "total_decisions": int,
        "auto_approved":   int,
        "coverage_pct":    float,
        "by_category": {
            "credential_access": {"total": X, "auto_approved": Y, "coverage_pct": Z},
            ...
        }
    }
    """
    try:
        rows = await neo4j_client.run_query(
            """
            MATCH (d:Decision)
            RETURN d.category AS category,
                   count(d) AS total,
                   sum(CASE WHEN d.auto_approved = true THEN 1 ELSE 0 END) AS approved
            """,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Neo4j query failed: {exc}")

    by_category: dict = {}
    grand_total    = 0
    grand_approved = 0

    for row in rows:
        cat      = row.get("category") or "unknown"
        total    = int(row.get("total") or 0)
        approved = int(row.get("approved") or 0)
        by_category[cat] = {
            "total":        total,
            "auto_approved": approved,
            "coverage_pct": round(approved / max(total, 1) * 100, 1),
        }
        grand_total    += total
        grand_approved += approved

    return {
        "total_decisions": grand_total,
        "auto_approved":   grand_approved,
        "coverage_pct":    round(grand_approved / max(grand_total, 1) * 100, 1),
        "by_category":     by_category,
    }


# ============================================================================
# Graph Explorer endpoints — Phase 8 (Tab 1 Panel B)
# ============================================================================

@router.post("/soc/graph/query")
async def graph_explorer_query(request: _GraphQueryRequest):
    """Run a validated read-only Cypher query.

    Body: {"cypher": "MATCH (n:User) RETURN n.name LIMIT 5"}

    Returns 400 if the query contains blocked mutation keywords.
    Returns {"rows": [...], "count": N, "query": str} on success.
    """
    from app.services.graph_explorer import GraphExplorerService
    result = await GraphExplorerService.run_safe_query(request.cypher, neo4j_client)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/soc/graph/top-nodes")
async def graph_top_nodes(
    type: Optional[str] = None,
    limit: int = 10,
):
    """Return top N most-connected nodes.

    Query params: ?type=User&limit=10 (both optional).
    Excludes :Decision and :Checkpoint nodes (internal bookkeeping).
    """
    from app.services.graph_explorer import GraphExplorerService
    nodes = await GraphExplorerService.get_top_nodes(
        neo4j_client, node_type=type, limit=limit
    )
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/soc/graph/node/{node_id}/neighbors")
async def graph_node_neighbors(node_id: str):
    """Return all neighbors of a specific node (up to 50).

    Response: {"node_id": str, "neighbors": [...], "total": int}
    """
    from app.services.graph_explorer import GraphExplorerService
    return await GraphExplorerService.get_node_neighbors(node_id, neo4j_client)


@router.get("/soc/graph/summary")
async def graph_summary():
    """Return node and relationship type counts for the explorer header.

    Response:
    {
        "total_nodes": int,
        "total_relationships": int,
        "node_types": {"Alert": N, "User": M, ...},
        "relationship_types": {"DECIDED_ON": N, ...},
    }
    """
    from app.services.graph_explorer import GraphExplorerService
    return await GraphExplorerService.get_graph_summary(neo4j_client)


@router.get("/soc/graph/prebuilt-queries")
async def graph_prebuilt_queries_list():
    """Return the catalogue of pre-built query names and descriptions.

    Response: {"queries": [...], "count": N}
    """
    from app.services.graph_explorer import GraphExplorerService
    queries = GraphExplorerService.list_prebuilt_queries()
    return {"queries": queries, "count": len(queries)}


@router.post("/soc/graph/prebuilt/{query_name}")
async def graph_run_prebuilt(query_name: str):
    """Run a pre-built query by name.

    Returns {"rows": [...], "count": N, "query": str}.
    Returns 404 if query_name is not in the catalogue.
    """
    from app.services.graph_explorer import GraphExplorerService
    result = await GraphExplorerService.run_prebuilt_query(query_name, neo4j_client)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ---------------------------------------------------------------------------
# GET /api/soc/learning-health  (P9 — Learning Health Monitor)
# ---------------------------------------------------------------------------

@router.get("/soc/learning-health")
async def learning_health():
    """Return learning health status based on conservation law monitoring.

    Evaluates alpha(t)*q(t)*V(t) >= theta_min (absolute floor) and
    relative-drop thresholds (baseline-2sigma=AMBER, baseline-3sigma=RED).

    Returns
    -------
    {
        status            : "GREEN" | "AMBER" | "RED" | "CALIBRATING",
        signal            : float,
        theta_min         : float,
        conservation      : {passed, status, headroom},
        components        : {alpha, q, V, n},
        baseline          : float | null,
        baseline_std      : float | null,
        red_days          : int,
        auto_pause_active : bool,
        interpretation    : str,
    }
    """
    from app.services.learning_health import LearningHealthMonitor
    return await LearningHealthMonitor.evaluate(neo4j_client)


# ============================================================================
# P22: Intervention Controls — EU AI Act Article 14 human oversight (L-12)
# ============================================================================

def _get_intervention_controls():
    """Build InterventionControls from existing singletons."""
    from app.services.gae_state import get_profile_scorer
    from app.services.checkpoint import checkpoint_svc
    from app.services.composite_gate import CompositeDiscriminant
    from app.services.intervention_controls import InterventionControls
    scorer = get_profile_scorer()
    return InterventionControls(
        db_client=neo4j_client,
        scorer=scorer,
        checkpoint_service=checkpoint_svc,
        composite_gate=CompositeDiscriminant,
    )


@router.post("/soc/interventions/freeze")
async def intervention_freeze(request: FreezeRequest):
    """Freeze all centroid learning globally."""
    try:
        ctrl = _get_intervention_controls()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return await ctrl.freeze_all_learning(request.initiated_by, request.reason)


@router.post("/soc/interventions/unfreeze")
async def intervention_unfreeze(request: FreezeRequest):
    """Resume centroid learning globally."""
    try:
        ctrl = _get_intervention_controls()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return await ctrl.unfreeze_all_learning(request.initiated_by, request.reason)


@router.post("/soc/interventions/rollback")
async def intervention_rollback(request: RollbackInterventionRequest):
    """Rollback to a centroid snapshot. preview=True returns what would change."""
    try:
        ctrl = _get_intervention_controls()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    result = await ctrl.rollback(
        request.snapshot_id, request.initiated_by, request.reason, request.preview
    )
    if "error" in result and not result.get("preview"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/soc/interventions/threshold")
async def intervention_threshold(request: ThresholdRequest):
    """Adjust auto-approve confidence threshold for a category."""
    try:
        ctrl = _get_intervention_controls()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return await ctrl.adjust_threshold(
        request.category, request.new_threshold, request.initiated_by, request.reason
    )


@router.get("/soc/interventions/state")
async def intervention_state():
    """Current state of all intervention controls."""
    try:
        ctrl = _get_intervention_controls()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return await ctrl.get_current_state()


@router.get("/soc/interventions/history")
async def intervention_history(limit: int = Query(50, ge=1, le=500)):
    """Intervention audit log."""
    try:
        ctrl = _get_intervention_controls()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    records = await ctrl.get_intervention_history(limit=limit)
    return {"interventions": records, "count": len(records)}


# ============================================================================
# GET /api/soc/frozen-roi — Frozen ROI Calculator (Adjustment E)
# ============================================================================

@router.get("/soc/frozen-roi")
async def frozen_roi(
    alerts_per_day: float = 200,
    analyst_hourly_cost: float = 85.0,
    auto_approve_rate: float = 0.04
):
    """Frozen mode ROI — value before learning is enabled."""
    from app.services.economics import FrozenROICalculator
    calc = FrozenROICalculator(
        analyst_hourly_cost=analyst_hourly_cost,
        alerts_per_day=alerts_per_day,
        auto_approve_rate=auto_approve_rate
    )
    return calc.compute()
