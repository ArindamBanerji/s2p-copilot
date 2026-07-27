"""Read-only S2P SOX audit export endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.domains.s2p.config import PENALTY_RATIO, S2PDomainConfig
from app.framework import audit
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.models.responses import GenericResponse
from app.services.receipt_store import get_receipt_store


router = APIRouter(prefix="/api/s2p/audit", tags=["s2p-audit"])

CSV_ROW_LIMIT = 1000
EXPORT_VERSION = "1.0"


def _graph_store(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is not None:
        return graph_store
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _sdk_scorer(request: Request) -> Any:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        raise HTTPException(status_code=500, detail="S2P scorer is not configured")
    return scorer


def _graph_reader(request: Request) -> S2PGraphReader:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    graph_store = getattr(scorer, "graph_store", None)
    reader = getattr(state, "s2p_graph_reader", None)
    if isinstance(reader, S2PGraphReader) and reader.store is graph_store:
        return reader
    if graph_store is None:
        raise HTTPException(status_code=503, detail="S2P graph reader unavailable")
    return S2PGraphReader(store=graph_store)


def _all_graph_decisions(reader: S2PGraphReader) -> list[dict[str, Any]]:
    rows = reader.get_all_decisions()
    return [dict(row) for row in rows if isinstance(row, dict)]


def _decision_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _decision_value(decision: dict[str, Any], *names: str) -> Any:
    metadata = _decision_metadata(decision)
    for name in names:
        if name in decision and decision.get(name) is not None:
            return decision.get(name)
        if name in metadata and metadata.get(name) is not None:
            return metadata.get(name)
    return None


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "correct", "confirmed", "success"}:
        return True
    if text in {"false", "0", "no", "incorrect", "overridden", "failed"}:
        return False
    return None


def _get_conservation_status(reader: S2PGraphReader, scorer: Any) -> dict[str, Any]:
    verified = reader.count_verified()
    correct = reader.count_correct()
    status = "UNKNOWN"
    try:
        phase = scorer.get_phase()
        status = str(getattr(phase, "status", phase) or "UNKNOWN")
    except Exception:
        status = "UNKNOWN"
    return {
        "verified": int(verified or 0),
        "correct": int(correct or 0),
        "override_rate": None,
        "theta_min": getattr(S2PDomainConfig, "tau", None),
        "q": getattr(S2PDomainConfig, "q_window", None),
        "status": status,
        "penalty_ratio": float(PENALTY_RATIO),
    }


def _get_iks(scorer: Any) -> dict[str, Any]:
    try:
        trajectory = scorer.trajectory()
    except Exception:
        return {}
    if isinstance(trajectory, dict):
        return dict(trajectory)
    if hasattr(trajectory, "model_dump"):
        return dict(trajectory.model_dump())
    if hasattr(trajectory, "__dict__"):
        return {
            key: value
            for key, value in vars(trajectory).items()
            if not key.startswith("_")
        }
    return {"value": str(trajectory)}


def _get_date_range(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [
        str(value)
        for decision in decisions
        for value in (_decision_value(decision, "timestamp", "created_at", "scored_at"),)
        if value
    ]
    if not timestamps:
        return {"first": None, "last": None}
    timestamps.sort()
    return {"first": timestamps[0], "last": timestamps[-1]}


def _compute_override_rate(decisions: list[dict[str, Any]]) -> float:
    comparable = 0
    overrides = 0
    for decision in decisions:
        scored = _decision_value(decision, "recommended_action", "action", "scored_action")
        human = _decision_value(decision, "human_action", "actual_action", "analyst_action")
        if scored is None or human is None:
            continue
        comparable += 1
        overrides += int(str(scored) != str(human))
    return round(overrides / comparable, 6) if comparable else 0.0


def _compute_financial_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    total_amount = 0.0
    total_at_risk = 0.0
    total_recovered = 0.0
    for decision in decisions:
        amount = _float_or_zero(_decision_value(decision, "amount", "total_amount"))
        at_risk = _float_or_zero(_decision_value(decision, "at_risk", "amount_at_risk"))
        reward = _float_or_zero(_decision_value(decision, "reward"))
        recovery_pct = _float_or_zero(_decision_value(decision, "recovery_pct"))
        total_amount += amount
        total_at_risk += at_risk
        if recovery_pct > 0 and at_risk > 0:
            total_recovered += at_risk * max(0.0, min(1.0, recovery_pct))
        elif reward > 0 and at_risk > 0:
            total_recovered += min(at_risk, reward * at_risk)
    return {
        "total_invoices_processed": len(decisions),
        "total_amount": round(total_amount, 2),
        "total_at_risk": round(total_at_risk, 2),
        "total_recovered": round(total_recovered, 2),
        "net_savings": round(total_recovered - total_at_risk, 2),
    }


def _factor_analysis(scorer: Any, decisions: list[dict[str, Any]]) -> dict[str, Any]:
    fingerprint: Any = {}
    try:
        raw_fingerprint = scorer.fingerprint()
        if isinstance(raw_fingerprint, dict):
            fingerprint = raw_fingerprint
        else:
            fingerprint = str(raw_fingerprint)
    except Exception:
        fingerprint = {}

    factor_totals = {name: 0.0 for name in S2PDomainConfig.factors}
    factor_counts = {name: 0 for name in S2PDomainConfig.factors}
    for decision in decisions:
        factors = _decision_value(decision, "factors")
        if not isinstance(factors, dict):
            factors = _decision_metadata(decision).get("factors")
        if not isinstance(factors, dict):
            continue
        for name in S2PDomainConfig.factors:
            if name in factors:
                factor_totals[name] += _float_or_zero(factors.get(name))
                factor_counts[name] += 1

    averages = {
        name: round(factor_totals[name] / factor_counts[name], 6)
        for name in S2PDomainConfig.factors
        if factor_counts[name]
    }
    return {
        "fingerprint": fingerprint,
        "factor_names": list(S2PDomainConfig.factors),
        "factor_averages": averages,
    }


def _audit_verification() -> dict[str, Any]:
    try:
        return {"available": True, "verification": audit.verify_chain()}
    except Exception as exc:
        return {
            "available": False,
            "verification": {
                "verified": False,
                "error": type(exc).__name__,
            },
        }


def _sox_compliance(chain: dict[str, Any], conservation: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    verification = dict(chain.get("verification") or {})
    verified = bool(verification.get("verified"))
    tamper_evidence = verification.get("tamper_evidence") or []
    conservation_enforced = str(conservation.get("status") or "UNKNOWN") != "UNKNOWN"
    audit_chain_complete = verified and bool(verification.get("entries_checked", 0) or decisions)
    recommendation = (
        "SOX-ready"
        if verified and conservation_enforced and audit_chain_complete
        else "insufficient_evidence"
    )
    return {
        "tamper_evident": verified and not tamper_evidence,
        "conservation_enforced": conservation_enforced,
        "audit_chain_complete": audit_chain_complete,
        "recommendation": recommendation,
        "status": recommendation,
        "sox_readiness": {
            "available": bool(decisions or verification.get("entries_checked", 0)),
            "note": "SOX readiness derived from audit chain, conservation proof, and decision availability.",
        },
    }


def _decision_chain() -> dict[str, Any]:
    chain = _audit_verification()
    try:
        entries = audit.get_audit_entries()
    except Exception:
        entries = []
    return {
        "entries_count": len(entries),
        **chain,
    }


def _export_payload(request: Request) -> dict[str, Any]:
    scorer = _sdk_scorer(request)
    reader = _graph_reader(request)
    graph_decisions = _all_graph_decisions(reader)
    audit_decisions = audit.get_decisions()
    decisions = graph_decisions if graph_decisions else audit_decisions
    verified_decisions = reader.count_verified()
    correct_decisions = reader.count_correct()
    conservation = _get_conservation_status(reader, scorer)
    override_rate = _compute_override_rate(decisions)
    conservation["override_rate"] = override_rate
    chain = _decision_chain()
    total = len(decisions)
    correct = int(correct_decisions or 0)

    return {
        "summary": {
            "export_timestamp": datetime.now(timezone.utc).isoformat(),
            "export_version": EXPORT_VERSION,
            "total_decisions": total,
            "verified_decisions": int(verified_decisions or 0),
            "correct_decisions": correct,
            "accuracy": round(correct / verified_decisions, 6) if verified_decisions else 0.0,
            "conservation_status": conservation["status"],
            "iks": _get_iks(scorer),
            "date_range": _get_date_range(decisions),
        },
        "decision_chain": chain,
        "conservation_proof": conservation,
        "factor_analysis": _factor_analysis(scorer, decisions),
        "financial_impact": _compute_financial_summary(decisions),
        "sox_compliance": _sox_compliance(chain, conservation, decisions),
        "source": "live",
        "engine": "s2p_audit_export",
    }


def _csv_row(decision: dict[str, Any]) -> list[Any]:
    actual = _decision_value(decision, "is_correct", "correct", "outcome")
    return [
        _decision_value(decision, "decision_id", "id") or "",
        _decision_value(decision, "timestamp", "created_at", "scored_at") or "",
        _decision_value(decision, "category") or "",
        _decision_value(decision, "recommended_action", "action", "scored_action") or "",
        _float_or_zero(_decision_value(decision, "confidence")),
        _bool_or_none(actual),
        _decision_value(decision, "conservation_status", "conservation_state") or "",
    ]


@router.get("/export", response_model=GenericResponse)
def export_audit_package(request: Request) -> dict[str, Any]:
    try:
        return _export_payload(request)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for audit export") from exc


@router.get("/export/csv", response_model=GenericResponse)
def export_audit_csv(request: Request) -> dict[str, Any]:
    try:
        decisions = _all_graph_decisions(_graph_reader(request))
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for audit export") from exc
    if not decisions:
        decisions = audit.get_decisions()
    capped = decisions[:CSV_ROW_LIMIT]
    headers = [
        "decision_id",
        "timestamp",
        "category",
        "action",
        "confidence",
        "is_correct",
        "conservation_status",
    ]
    return {
        "headers": headers,
        "rows": [_csv_row(decision) for decision in capped],
        "total": len(decisions),
        "exported": len(capped),
        "format": "json_tabular",
        "note": f"Rows capped at {CSV_ROW_LIMIT}.",
    }
