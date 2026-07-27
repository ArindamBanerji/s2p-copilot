"""Read-only S2P governance, compliance, and rationalization endpoints."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.models.responses import GenericResponse
from app.graph.s2p_graph_reader import GraphUnavailableError
from app.routers.s2p_data_helpers import load_suppliers
from app.services.receipt_store import get_receipt_store


router = APIRouter(prefix="/api/s2p/governance", tags=["s2p-governance"])


def _safe_conservation_snapshot(request: Request) -> dict[str, Any]:
    try:
        from app.routers.s2p import (
            _current_conservation_status,
            _graph_verified_counts,
            _s2p_graph_reader,
        )

        verified_count, correct_count = _graph_verified_counts(request)
        total_decisions = len(_s2p_graph_reader(request).get_all_decisions())
        return {
            "state": _current_conservation_status(request),
            "verified_count": verified_count,
            "correct_count": correct_count,
            "total_decisions": max(int(total_decisions), 0),
        }
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for governance") from exc
    except Exception:
        return {
            "state": "UNKNOWN",
            "verified_count": 0,
            "correct_count": 0,
            "total_decisions": 0,
        }


def _receipt_conservation_field(receipt: dict[str, Any], which: str) -> Any:
    nested = receipt.get(f"conservation_{which}")
    if nested:
        return nested
    return receipt.get(f"conservation_state_{which}")


def _check_receipt_compliance(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    receipt_id = str(receipt.get("receipt_id") or "unknown")
    gaps: list[dict[str, Any]] = []
    if not receipt.get("receipt_hash"):
        gaps.append({"receipt_id": receipt_id, "issue": "missing_hash", "severity": "high"})

    factor_vector = receipt.get("factor_vector")
    if not isinstance(factor_vector, list) or len(factor_vector) != 7:
        gaps.append({"receipt_id": receipt_id, "issue": "incomplete_factors", "severity": "medium"})

    before = _receipt_conservation_field(receipt, "before")
    after = _receipt_conservation_field(receipt, "after")
    if not before and not after:
        gaps.append({"receipt_id": receipt_id, "issue": "no_conservation_snapshot", "severity": "medium"})
    return gaps


def _chain_verified(chain: dict[str, Any]) -> bool:
    return bool(chain.get("verified", chain.get("valid", False)))


def _screen_receipts(request: Request) -> dict[str, Any]:
    store = get_receipt_store()
    receipts = store.get_chain(limit=10000)
    chain = store.verify_chain()
    stats = store.stats
    conservation = _safe_conservation_snapshot(request)

    gaps: list[dict[str, Any]] = []
    receipts_with_gaps = 0
    for receipt in receipts:
        receipt_gaps = _check_receipt_compliance(receipt)
        if receipt_gaps:
            receipts_with_gaps += 1
            gaps.extend(receipt_gaps)

    total = len(receipts)
    compliant = total - receipts_with_gaps
    human_oversight_documented = all(receipt.get("human_action") for receipt in receipts)
    automated_decision_logged = all(receipt.get("scored_action") and receipt.get("receipt_hash") for receipt in receipts)
    conservation_proof_available = conservation.get("state") not in {"", "UNKNOWN"}
    sox_flags = {
        "hash_chain_valid": _chain_verified(chain),
        "override_distribution_available": {"overrides", "confirms"} <= set(stats),
        "conservation_proof_available": conservation_proof_available,
    }
    sox_score = round(sum(1 for value in sox_flags.values() if value) / len(sox_flags), 4)

    return {
        "screening_timestamp": datetime.now(timezone.utc).isoformat(),
        "total_decisions_screened": total,
        "compliant": compliant,
        "with_gaps": receipts_with_gaps,
        "compliance_rate": round(compliant / total, 6) if total else 1.0,
        "chain_integrity": chain,
        "conservation_state": conservation,
        "receipt_stats": stats,
        "gaps": gaps[:50],
        "eu_ai_act": {
            "article_14_traceable": _chain_verified(chain) and not gaps,
            "human_oversight_documented": human_oversight_documented,
            "automated_decision_logged": automated_decision_logged,
        },
        "sox_readiness": {**sox_flags, "score": sox_score},
    }


@router.get("/compliance-screening", response_model=GenericResponse)
def compliance_screening(request: Request) -> dict[str, Any]:
    return _screen_receipts(request)


@router.get("/compliance-gaps", response_model=GenericResponse)
def compliance_gaps() -> dict[str, Any]:
    receipts = get_receipt_store().get_chain(limit=10000)
    gaps = [gap for receipt in receipts for gap in _check_receipt_compliance(receipt)]
    issue_summary = dict(sorted(Counter(gap["issue"] for gap in gaps).items()))
    return {"total_gaps": len(gaps), "issue_summary": issue_summary, "gaps": gaps[:100]}


@router.get("/conservation-proof", response_model=GenericResponse)
def conservation_proof(request: Request) -> dict[str, Any]:
    snapshot = _safe_conservation_snapshot(request)
    receipts = get_receipt_store().get_chain(limit=10000)
    transitions: Counter[str] = Counter()
    for receipt in receipts:
        before = _receipt_conservation_field(receipt, "before") or "UNKNOWN"
        after = _receipt_conservation_field(receipt, "after") or "UNKNOWN"
        transitions[f"{before}->{after}"] += 1
    return {
        "current_state": snapshot["state"],
        "verified_count": snapshot["verified_count"],
        "correct_count": snapshot["correct_count"],
        "total_decisions": snapshot["total_decisions"],
        "state_transitions": dict(sorted(transitions.items())),
        "proof_complete": snapshot["state"] != "UNKNOWN" and get_receipt_store().verify_chain().get("verified") is True,
    }


@router.get("/sox-readiness", response_model=GenericResponse)
def sox_readiness(request: Request) -> dict[str, Any]:
    screening = _screen_receipts(request)
    sox = dict(screening.get("sox_readiness") or {})
    chain = dict(screening.get("chain_integrity") or {})
    conservation = dict(screening.get("conservation_state") or {})
    receipt_stats = dict(screening.get("receipt_stats") or {})
    total = int(screening.get("total_decisions_screened") or 0)
    gaps = int(screening.get("with_gaps") or 0)
    raw_score = float(sox.get("score") or 0.0)
    readiness_score = round(max(0.0, min(100.0, raw_score * 100.0)), 2)
    components = {
        "audit_chain": {
            "ready": bool(sox.get("hash_chain_valid")),
            "chain_length": int(chain.get("chain_length") or 0),
            "last_hash": chain.get("last_hash") or "",
        },
        "tamper_check": {
            "ready": bool(chain.get("verified")),
            "broken_at_index": chain.get("broken_at_index"),
        },
        "conservation": {
            "ready": bool(sox.get("conservation_proof_available")),
            "state": conservation.get("state") or "UNKNOWN",
            "verified_count": int(conservation.get("verified_count") or 0),
            "correct_count": int(conservation.get("correct_count") or 0),
        },
        "volume": {
            "ready": total > 0 and gaps == 0,
            "total_receipts": total,
            "total_decisions_screened": total,
            "compliance_rate": screening.get("compliance_rate", 1.0),
            "overrides": int(receipt_stats.get("overrides") or 0),
            "confirms": int(receipt_stats.get("confirms") or 0),
        },
    }
    recommendation = "SOX-ready" if readiness_score >= 100.0 and gaps == 0 and total > 0 else "Not yet ready"
    return {
        "screening_timestamp": screening.get("screening_timestamp"),
        "sox_readiness_score": readiness_score,
        "components": components,
        "recommendation": recommendation,
        "total_decisions_screened": total,
        "gaps": screening.get("gaps", []),
    }


def _load_supplier_data() -> list[dict[str, Any]]:
    return load_suppliers()


def _extract_nested_float(supplier: dict[str, Any], field: str) -> float:
    value = supplier.get(field)
    if isinstance(value, dict):
        for key in ("current", "value", "q3", "actual_q4", "baseline", "q1_q2", "contractual"):
            if key in value:
                try:
                    return float(value[key])
                except (TypeError, ValueError):
                    return 0.0
        return 0.0
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _supplier_exception_rate(supplier: dict[str, Any]) -> float:
    return _extract_nested_float(supplier, "exception_rate")


def _supplier_otif(supplier: dict[str, Any]) -> float:
    if "otif_score" in supplier:
        return _extract_nested_float(supplier, "otif_score")
    return _extract_nested_float(supplier, "otif")


def _supplier_trend(supplier: dict[str, Any]) -> str:
    return str(supplier.get("recent_trend") or supplier.get("financial_health_trend") or "stable")


def _supplier_invoice_volume(supplier: dict[str, Any]) -> int:
    for field in ("total_invoices", "total_invoices_ytd"):
        if field not in supplier:
            continue
        try:
            return max(int(supplier.get(field) or 0), 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _supplier_name(supplier: dict[str, Any]) -> Any:
    return supplier.get("supplier_name") or supplier.get("name") or supplier.get("supplier_id")


def _supplier_region(supplier: dict[str, Any]) -> str:
    return str(supplier.get("region") or "unknown")


def _classify_supplier(supplier: dict[str, Any]) -> dict[str, Any]:
    exception_rate = _supplier_exception_rate(supplier)
    otif = _supplier_otif(supplier)
    trend = _supplier_trend(supplier)

    if trend == "declining" and exception_rate > 0.15:
        recommendation = "phase_out"
        reason = "Declining financial health with elevated exception rate."
        action = "Start alternate-source plan and limit new awards."
    elif trend == "declining" and otif < 0.75:
        recommendation = "phase_out"
        reason = "Declining financial health with weak current OTIF."
        action = "Shift volume to stronger suppliers."
    elif otif >= 0.90 and exception_rate <= 0.10:
        recommendation = "grow"
        reason = "Strong OTIF and controlled exception rate."
        action = "Consider incremental volume consolidation."
    elif trend == "improving":
        recommendation = "grow"
        reason = "Improving supplier trend supports measured growth."
        action = "Pilot additional volume with monitoring."
    else:
        recommendation = "maintain"
        reason = "Supplier performance is mixed but manageable."
        action = "Maintain current allocation and monitor."

    return {
        "supplier_id": supplier.get("supplier_id"),
        "name": _supplier_name(supplier),
        "recommendation": recommendation,
        "exception_rate": round(exception_rate, 6),
        "otif": round(otif, 6),
        "trend": trend,
        "region": _supplier_region(supplier),
        "total_invoices": _supplier_invoice_volume(supplier),
        "reason": reason,
        "action": action,
    }


def _estimate_savings(phase_out: list[dict[str, Any]], all_suppliers: list[dict[str, Any]]) -> dict[str, Any]:
    suppliers_by_id = {supplier.get("supplier_id"): supplier for supplier in all_suppliers}
    phase_out_suppliers = [
        suppliers_by_id[row.get("supplier_id")]
        for row in phase_out
        if row.get("supplier_id") in suppliers_by_id
    ]
    total_volume = sum(_supplier_invoice_volume(supplier) for supplier in all_suppliers)
    phase_out_volume = sum(_supplier_invoice_volume(supplier) for supplier in phase_out_suppliers)
    annual = sum(
        _supplier_exception_rate(supplier) * _supplier_invoice_volume(supplier) * 500
        for supplier in phase_out_suppliers
    )
    annual_savings = round(annual, 2)
    quarterly_savings = round(annual_savings / 4, 2)
    return {
        "currency": "USD",
        "estimated_quarterly_savings": quarterly_savings,
        "estimated_annual_savings": annual_savings,
        "phase_out_invoice_volume": phase_out_volume,
        "total_invoice_volume": total_volume,
        "suppliers_affected": len(phase_out_suppliers),
        "basis": "phase-out supplier exception rate multiplied by fixture invoice volume and demo exception handling cost",
    }


def _recommendations() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    suppliers = _load_supplier_data()
    return suppliers, [_classify_supplier(supplier) for supplier in suppliers]


@router.get("/rationalization", response_model=GenericResponse)
def rationalization() -> dict[str, Any]:
    suppliers, recommendations = _recommendations()
    buckets: dict[str, list[dict[str, Any]]] = {
        "grow": [],
        "maintain": [],
        "phase_out": [],
    }
    for recommendation in recommendations:
        buckets[recommendation["recommendation"]].append(recommendation)

    return {
        "total_suppliers": len(suppliers),
        "grow": len(buckets["grow"]),
        "maintain": len(buckets["maintain"]),
        "phase_out": len(buckets["phase_out"]),
        "recommendations": recommendations,
        "estimated_savings": _estimate_savings(buckets["phase_out"], suppliers),
    }


@router.get("/rationalization/overlap", response_model=GenericResponse)
def rationalization_overlap() -> dict[str, Any]:
    suppliers, recommendations = _recommendations()
    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_id = {row["supplier_id"]: row for row in recommendations}
    for supplier in suppliers:
        region = _supplier_region(supplier)
        by_region[region].append(by_id.get(supplier.get("supplier_id"), _classify_supplier(supplier)))

    groups: list[dict[str, Any]] = [
        {
            "overlap_key": region,
            "basis": "region",
            "supplier_ids": [row["supplier_id"] for row in rows],
            "supplier_count": len(rows),
            "phase_out_candidates": [
                row["supplier_id"] for row in rows if row["recommendation"] == "phase_out"
            ],
        }
        for region, rows in sorted(by_region.items())
        if len(rows) > 1
    ]
    return {
        "overlap_groups": groups,
        "total_groups": len(groups),
        "consolidation_candidates": sum(len(group["phase_out_candidates"]) for group in groups),
    }


@router.get("/rationalization/supplier/{supplier_id}", response_model=GenericResponse)
def rationalization_supplier(supplier_id: str) -> dict[str, Any]:
    for supplier in _load_supplier_data():
        if supplier.get("supplier_id") == supplier_id:
            return {"supplier": supplier, "recommendation": _classify_supplier(supplier)}
    raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
