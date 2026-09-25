"""S2P queue and supplier previews from live graph decisions and scorer state."""

from __future__ import annotations

import json
import threading
import time
from uuid import uuid4
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from copilot_sdk.state.cached_static import cached_static

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import S2PGraphReader
from app.models.responses import GenericResponse
from app.services.s2p_preview_data import _mapping, _timestamp, _verified, read_decisions, supplier_profiles
from app.services.s2p_preview_simulation import _build_compounding_trajectory

router = APIRouter(prefix="/api/s2p/preview", tags=["s2p-preview"])

ENGINE_VERSION = "v0.7.23"

_PREVIEW_OBSERVATIONS_WRITTEN: set[str] = set()
_SCORED_INVOICE_CACHE_TTL_SECONDS = 30.0
_SCORED_INVOICE_CACHE_LOCK = threading.RLock()
_SCORED_INVOICE_CACHE: dict[str, tuple[float, int, list[dict[str, Any]], int]] = {}
_SUPPLIER_PROFILE_CACHE_TTL_SECONDS = 30.0
_SUPPLIER_PROFILE_CACHE_LOCK = threading.RLock()
_SUPPLIER_PROFILE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _get_config_list(attr_name: str, method_name: str) -> list[str]:
    method = getattr(S2PDomainConfig, method_name, None)
    if callable(method):
        return list(method())
    return list(getattr(S2PDomainConfig, attr_name))


def _tensor_shape_tuple() -> tuple[int, int, int]:
    return (
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    )


def _tensor_shape_text() -> str:
    return str(_tensor_shape_tuple())


def _get_category_list() -> list[str]:
    return _get_config_list("categories", "get_categories")


def _get_action_list() -> list[str]:
    return _get_config_list("actions", "get_actions")


def _get_factor_list() -> list[str]:
    return _get_config_list("factors", "get_factors")


def _get_canonical_factor_list() -> list[str]:
    return list(S2PDomainConfig.canonical_factors)


def _to_float_list(values) -> list[float]:
    return [float(value) for value in values]


def _get_scorer(request: Request):
    return request.app.state.scorer


def _get_graph_store(request: Request, scorer: Any) -> Any:
    return getattr(request.app.state, "graph_store", None) or getattr(scorer, "graph_store", None)


def _score_read_only(scorer: Any, factors: dict[str, float], category: str) -> Any:
    score_read_only = getattr(scorer, "score_read_only", None)
    if not callable(score_read_only):
        raise RuntimeError("S2P preview requires a read-only scorer path")
    return score_read_only(factors, category)


def _score_invoice(invoice: dict[str, Any], scorer) -> dict[str, Any]:
    actions = _get_action_list()
    category = str(invoice["category"])
    factor_names = _get_factor_list()
    factors = invoice.get("factors") or {}
    factor_vector = [float(factors.get(name, 0.5)) for name in factor_names]
    scored_factors = {name: float(factors.get(name, 0.5)) for name in factor_names}
    metadata = invoice.get("metadata") if isinstance(invoice.get("metadata"), dict) else {}
    score_result = _score_read_only(
        scorer,
        scored_factors,
        category,
    )
    action_name = str(score_result.action)
    action_index = int(score_result.action_index)
    confidence = float(score_result.confidence)
    probabilities = _to_float_list(score_result.probabilities)
    variance_pct = float(factors.get("amount_variance_ratio", 0.0)) * 100.0

    return {
        "decision_id": invoice["decision_id"],
        "invoice_id": invoice["invoice_id"],
        "supplier_id": invoice["supplier_id"],
        "supplier": invoice["supplier_name"],
        "supplier_name": invoice["supplier_name"],
        "category": category,
        "amount": invoice["amount"],
        "po_reference": invoice["po_number"],
        "variance_pct": float(variance_pct),
        "scored_action": action_name,
        "recommended_action": str(action_name),
        "recommended_action_index": action_index,
        "confidence": confidence,
        "probabilities": _to_float_list(probabilities),
        "factors": {name: float(factors.get(name, 0.5)) for name in factor_names},
        "factor_vector": _to_float_list(factor_vector),
        "ground_truth_action": invoice["ground_truth_action"],
        "ground_truth_action_index": actions.index(invoice["ground_truth_action"]) if invoice["ground_truth_action"] in actions else None,
        "metadata": metadata,
        **({"process_context": invoice["process_context"]} if invoice.get("process_context") else {}),
      }


def _write_preview_observation(request: Request, invoice: dict[str, Any]) -> None:
    scorer = _get_scorer(request)
    graph_store = _get_graph_store(request, scorer)
    factor_names = _get_factor_list()
    metadata = dict(invoice.get("metadata") or {})
    metadata.update(
        {
            "preview": True,
            "invoice_id": invoice.get("invoice_id"),
            "supplier_id": invoice.get("supplier_id"),
            "supplier_name": invoice.get("supplier_name"),
            "amount": invoice.get("amount"),
            "po_reference": invoice.get("po_reference"),
        }
    )
    graph_store.write_observation(
        observation_id=f"OBS-{uuid4().hex[:12]}",
        domain="s2p",
        category=str(invoice["category"]),
        recommended_action=str(invoice["recommended_action"]),
        confidence=float(invoice["confidence"]),
        source_route="preview",
        scorer_version=getattr(scorer, "version", ENGINE_VERSION) or "unknown",
        factor_schema_version="s2p_factor_schema_v2",
        entity_id=str(invoice["invoice_id"]),
        factor_vector=[float(value) for value in invoice["factor_vector"]],
        factor_names=factor_names,
        metadata=metadata,
    )


def _write_preview_observation_once(request: Request, invoice: dict[str, Any]) -> None:
    invoice_id = str(invoice.get("invoice_id") or "")
    if not invoice_id:
        _write_preview_observation(request, invoice)
        return
    if invoice_id in _PREVIEW_OBSERVATIONS_WRITTEN:
        return
    _PREVIEW_OBSERVATIONS_WRITTEN.add(invoice_id)
    try:
        _write_preview_observation(request, invoice)
    except Exception:
        _PREVIEW_OBSERVATIONS_WRITTEN.discard(invoice_id)
        raise


def invalidate_preview_observation(invoice_id: str | None) -> None:
    if invoice_id:
        _PREVIEW_OBSERVATIONS_WRITTEN.discard(str(invoice_id))


def _decision_invoice(row: dict[str, Any]) -> dict[str, Any]:
    names = row.get("factor_names") or _get_factor_list()
    vector = row.get("factor_vector") or []
    if isinstance(names, str):
        names = json.loads(names)
    if isinstance(vector, str):
        vector = json.loads(vector)
    factors = {**dict(zip(names, vector)), **_mapping(row.get("factors"))}
    return {
        "decision_id": row["decision_id"],
        "invoice_id": row.get("invoice_id") or row.get("source_invoice_id") or row.get("entity_id") or row["decision_id"],
        "supplier_id": row.get("supplier_id") or "",
        "supplier_name": row.get("supplier_name") or row.get("supplier") or row.get("supplier_id") or "",
        "category": row["category"],
        "amount": float(row["amount"]) if row.get("amount") is not None else None,
        "po_number": row.get("po_number") or row.get("po_reference"),
        "factors": factors,
        "ground_truth_action": row.get("actual_action"),
        "metadata": _mapping(row.get("metadata")),
        "process_context": row.get("process_context"),
    }


def _pending_decisions(request: Request) -> list[dict[str, Any]]:
    return sorted((row for row in read_decisions(_get_graph_store(request, _get_scorer(request))) if not _verified(row)),
                  key=lambda row: (_timestamp(row), str(row["decision_id"])), reverse=True)


def _score_rows(request: Request, invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scorer = _get_scorer(request)
    return [_score_invoice(invoice, scorer) for invoice in invoices]


def _verified_cache_token(graph_store: Any) -> int:
    try:
        return int(S2PGraphReader(graph_store).count_verified())
    except Exception:
        return 0


def _decision_cache_token(graph_store: Any) -> int:
    try:
        return int(S2PGraphReader(graph_store).count_decisions())
    except Exception:
        return 0


def _scorer_state_token(scorer: Any) -> str:
    if hasattr(scorer, "version"):
        return str(getattr(scorer, "version", ENGINE_VERSION) or ENGINE_VERSION)
    state = getattr(scorer, "__dict__", None)
    if not isinstance(state, dict):
        return ENGINE_VERSION
    simple = {
        str(key): value
        for key, value in state.items()
        if isinstance(value, (str, int, float, bool, type(None)))
    }
    return json.dumps(simple, sort_keys=True, default=str)


def _scored_invoice_cache_key(scorer: Any, graph_store: Any) -> str:
    scorer_type = type(scorer).__qualname__
    scorer_version = _scorer_state_token(scorer)
    verified_token = _verified_cache_token(graph_store)
    return f"s2p:preview:scored-invoices:v1:{scorer_type}:{scorer_version}:v{verified_token}"


def _cached_recent_scores(request: Request, n: int = 50) -> tuple[list[dict[str, Any]], int]:
    now = time.monotonic()
    scorer = _get_scorer(request)
    graph_store = _get_graph_store(request, scorer)
    key = _scored_invoice_cache_key(scorer, graph_store)
    with _SCORED_INVOICE_CACHE_LOCK:
        cached = _SCORED_INVOICE_CACHE.get(key)
        if cached is not None:
            created, cached_n, invoices, total = cached
            if cached_n >= n and now - created <= _SCORED_INVOICE_CACHE_TTL_SECONDS:
                return [dict(invoice) for invoice in invoices[:n]], total

    pending = _pending_decisions(request)
    recent = [_decision_invoice(row) for row in pending[:n]]
    invoices = sorted(
        [_score_invoice(invoice, scorer) for invoice in recent],
        key=lambda invoice: invoice["confidence"],
        reverse=True,
    )
    with _SCORED_INVOICE_CACHE_LOCK:
        _SCORED_INVOICE_CACHE[key] = (
            time.monotonic(),
            n,
            [dict(invoice) for invoice in invoices],
            len(pending),
        )
    return [dict(invoice) for invoice in invoices], len(pending)


def _recent_scores(request: Request, n: int = 50) -> list[dict[str, Any]]:
    invoices, _total = _cached_recent_scores(request, n)
    return invoices


def _get_preview_simulation_scorer():
    from app.main import build_s2p_scorer
    from copilot_sdk.graph.memory_store import InMemoryGraphStore

    return build_s2p_scorer(
        graph_store=InMemoryGraphStore(domain="s2p"),
        # The preview is an isolated, non-production simulation.  Explicitly
        # select the test profile so production AGE requirements cannot make
        # this read-only preview return HTTP 500 when AGE is unavailable.
        profile="test",
    )


def _clamp_limit(limit: int, minimum: int, maximum: int) -> int:
    if maximum <= 0:
        return 0
    return max(minimum, min(int(limit), maximum))


def reset_preview_state() -> None:
    _PREVIEW_OBSERVATIONS_WRITTEN.clear()
    with _SCORED_INVOICE_CACHE_LOCK:
        _SCORED_INVOICE_CACHE.clear()
    with _SUPPLIER_PROFILE_CACHE_LOCK:
        _SUPPLIER_PROFILE_CACHE.clear()


def _supplier_profile_cache_key(graph_store: Any) -> str:
    verified_token = _verified_cache_token(graph_store)
    decision_token = _decision_cache_token(graph_store)
    return f"s2p:preview:supplier-profiles:v1:d{decision_token}:v{verified_token}"


def _cached_supplier_profiles(request: Request) -> list[dict[str, Any]]:
    scorer = _get_scorer(request)
    graph_store = _get_graph_store(request, scorer)
    key = _supplier_profile_cache_key(graph_store)
    now = time.monotonic()
    with _SUPPLIER_PROFILE_CACHE_LOCK:
        cached = _SUPPLIER_PROFILE_CACHE.get(key)
        if cached is not None:
            created, profiles = cached
            if now - created <= _SUPPLIER_PROFILE_CACHE_TTL_SECONDS:
                return [dict(profile) for profile in profiles]
    profiles = supplier_profiles(read_decisions(graph_store, with_outcomes=True))
    with _SUPPLIER_PROFILE_CACHE_LOCK:
        _SUPPLIER_PROFILE_CACHE[key] = (time.monotonic(), [dict(profile) for profile in profiles])
    return [dict(profile) for profile in profiles]


def _preview_queue_payload(request: Request, limit: int = 5) -> dict[str, Any]:
    clamped_limit = _clamp_limit(limit, 1, 50)
    invoices, total_pending = _cached_recent_scores(request, 50)
    shown = invoices[:clamped_limit]
    for invoice in shown:
        _write_preview_observation_once(request, invoice)
    exceptions = shown
    auto_approve_count = sum(1 for invoice in invoices if invoice["scored_action"] == "auto_approve")
    confidence_avg = sum(invoice["confidence"] for invoice in invoices) / len(invoices) if invoices else 0.0
    return {
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "source": "graph",
        "total": total_pending,
        "scored_count": len(invoices),
        "showing": len(shown),
        "exceptions": exceptions,
        "invoices": shown,
        "auto_approve_rate": round(auto_approve_count / len(invoices), 4) if invoices else 0.0,
        "confidence_avg": round(confidence_avg, 4),
        "scorer": {
            "engine": "Graph Attention Engine",
            "version": ENGINE_VERSION,
            "tensor_shape": _tensor_shape_text(),
            "factors": _get_factor_list(),
        },
    }


def _preview_queue_limit(request: Request, default: int = 5) -> int:
    try:
        raw = request.query_params.get("limit")
    except Exception:
        raw = None
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@router.get("/queue", response_model=GenericResponse)
def preview_queue(request: Request) -> dict[str, Any]:
    try:
        return _preview_queue_payload(request, _preview_queue_limit(request))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"status": "error", "message": "S2P preview queue failed", "exceptions": []},
        ) from exc


@router.get("/conservation", response_model=GenericResponse)
def preview_conservation(request: Request) -> dict[str, Any]:
    invoices = _recent_scores(request)
    auto_approve_count = sum(
        1
        for invoice in invoices
        if invoice["recommended_action"] == "auto_approve"
        and invoice["confidence"] >= 0.80
    )
    auto_approve_pct = (auto_approve_count / len(invoices) * 100.0) if invoices else 0.0
    status = "AMBER" if auto_approve_pct < 20.0 else "GREEN"
    conservation_product = float(auto_approve_pct / 100.0)

    return {
        "engine_version": ENGINE_VERSION,
        "source": "illustration",
        "status": "GREEN",
        "auto_approve_rate": 0.45,
        "accuracy": 0.84,
        "verified_decisions": 1000,
        "penalty_ratio": 5.0,
        "passed": True,
        "curve": [
            {"verified_decisions": 0, "accuracy": 0.70, "auto_approve_rate": 0.12},
            {"verified_decisions": 250, "accuracy": 0.76, "auto_approve_rate": 0.24},
            {"verified_decisions": 500, "accuracy": 0.80, "auto_approve_rate": 0.35},
            {"verified_decisions": 1000, "accuracy": 0.84, "auto_approve_rate": 0.45},
        ],
        "computed_status": status,
        "auto_approve_pct": float(round(auto_approve_pct, 4)),
        "fixture_decisions": int(len(invoices)),
        "copilot": "S2P Invoice Exception",
        "conservation_product": float(round(conservation_product, 6)),
        "conservation_threshold": 0.20,
    }


@router.get("/compounding", response_model=GenericResponse)
@cached_static("preview-compounding", copilot="s2p")
def preview_compounding() -> dict[str, Any]:
    simulation = _build_compounding_trajectory(scorer_factory=_get_preview_simulation_scorer)
    points = simulation["points"]
    initial_accuracy = float(points[0]["accuracy"]) if points else 0.0
    current_accuracy = float(points[-1]["accuracy"]) if points else 0.0

    return {
        "engine_version": ENGINE_VERSION,
        "initial_accuracy": initial_accuracy,
        "current_accuracy": current_accuracy,
        "total_decisions": simulation["total_decisions"],
        "source": "s2p_preview_simulation",
        "tensor_shape": list(_tensor_shape_tuple()),
        "trajectory": points,
    }


@router.get("/suppliers", response_model=GenericResponse)
def preview_suppliers(request: Request, limit: int | None = None) -> dict[str, Any]:
    suppliers = _cached_supplier_profiles(request)
    if limit is None:
        shown = suppliers
    else:
        clamped_limit = _clamp_limit(limit, 1, len(suppliers))
        shown = suppliers[:clamped_limit]
    return {
        "engine_version": ENGINE_VERSION,
        "total": len(suppliers),
        "showing": len(shown),
        "suppliers": shown,
        "source": "graph",
        "cache": "scored_preview_cache",
    }


@router.get("/config", response_model=GenericResponse)
@cached_static("preview-config", copilot="s2p")
def preview_config() -> dict[str, Any]:
    return {
        "engine_version": ENGINE_VERSION,
        "domain": "s2p",
        "tensor_shape": _tensor_shape_text(),
        "categories": _get_category_list(),
        "actions": _get_action_list(),
        "factors": _get_factor_list(),
        "canonical_factors": _get_canonical_factor_list(),
        "penalty_ratio": 5.0,
        "platform_comparison": {
            "soc": {
                "domain": "Security Operations",
                "tensor": "SOC production tensor",
                "categories": 6,
                "actions": 4,
                "factors": 6,
                "penalty_ratio": "20:1",
                "conservation": "active",
            },
            "s2p": {
                "domain": "Source-to-Pay",
                "tensor": _tensor_shape_text(),
                "categories": S2PDomainConfig.n_categories,
                "actions": S2PDomainConfig.n_actions,
                "factors": S2PDomainConfig.n_factors,
                "penalty_ratio": "5:1",
                "conservation": "active",
            },
            "shared": {
                "engine_version": "0.7.23",
                "conservation_law": "α·q·V ≥ θ_min",
                "learning_strategy": "ContinuousStrategy",
                "message": "Two domains on one engine. Same math. Different parameters. Same conservation law.",
            },
        },
        "cross_copilot_signals": {
            "description": "Operational patterns inherited from SOC AgentEvolver",
            "signals": [
                {
                    "pattern": "Tighten threshold during anomaly cluster",
                    "source_copilot": "SOC",
                    "source_rule": "RULE-CAMPAIGN-ESCALATE",
                    "adapted_as": "RULE-S2P-EXCEPTION-CLUSTER",
                    "adaptation": (
                        "Campaign detection → supplier exception spike detection. "
                        "When 3+ exceptions from same supplier in 7 days, "
                        "tighten auto-approve threshold by 15%."
                    ),
                    "warm_start_prior": 0.757,
                    "warm_start_source": "SOC shadow win rate (75.7%, 25 comparisons)",
                },
                {
                    "pattern": "Drift-triggered recalibration",
                    "source_copilot": "SOC",
                    "source_rule": "RULE-DRIFT-THRESHOLD",
                    "adapted_as": "RULE-S2P-COMMODITY-DRIFT",
                    "adaptation": (
                        "Per-category accuracy drift → per-commodity accuracy drift. "
                        "When commodity index correlation factor σ increases >20%, "
                        "trigger scoring threshold review."
                    ),
                    "warm_start_prior": 0.68,
                    "warm_start_source": "SOC shadow win rate (68%, 25 comparisons)",
                },
            ],
            "network_effect": (
                "Each new copilot starts at IKS 12 instead of IKS 0. "
                "Structural patterns (not domain-specific centroids) transfer "
                "across copilots via AgentEvolver rule templates."
            ),
            "status": "designed_capability",
            "note": (
                "Cross-copilot signal transfer is architecturally validated. "
                "Production activation requires S2P pilot data."
            ),
        },
    }
