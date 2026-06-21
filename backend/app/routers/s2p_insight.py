"""S2P insight endpoints for invoice fingerprints and process signals."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors
from app.models.responses import GenericResponse
from app.routers.s2p_data_helpers import find_invoice, load_invoices, load_suppliers

router = APIRouter(prefix="/api/s2p/insight", tags=["s2p-insight"])

_load_invoices = load_invoices
_load_suppliers = load_suppliers

_PROCESS_ACTIVITY_TEMPLATE: tuple[dict[str, Any], ...] = (
    {"name": "PO Creation", "pct_of_total": 15.0, "system": "SAP S/4HANA"},
    {"name": "Goods Receipt", "pct_of_total": 20.0, "system": "SAP S/4HANA"},
    {"name": "Invoice Receipt", "pct_of_total": 10.0, "system": "SAP S/4HANA"},
    {"name": "Three-Way Match", "pct_of_total": 30.0, "system": "SAP S/4HANA"},
    {"name": "Exception Resolution", "pct_of_total": 15.0, "system": "S2P Copilot"},
    {"name": "Payment Release", "pct_of_total": 10.0, "system": "SAP S/4HANA"},
)

_BOTTLENECK_REASONS: dict[str, str] = {
    "contract_gap": "Contract terms require buyer review before match completion.",
    "duplicate_risk": "Potential duplicate invoice requires exception handling.",
    "price_variance": "Price variance slows three-way match approval.",
    "quantity_mismatch": "Quantity mismatch requires goods receipt reconciliation.",
    "format_compliance": "Invoice format compliance requires manual validation.",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _data_path(filename: str) -> Path:
    return _repo_root() / "data" / filename


def _load_candidate_json(path: Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    return data


def _load_celonis() -> dict[str, Any]:
    candidates: list[Path] = [_data_path("celonis_process_data.json")]
    sdk_root = os.environ.get("CLAUDE_SDK", "")
    if sdk_root:
        candidates.append(Path(sdk_root) / "apps" / "dataops" / "backend" / "data" / "celonis_process_data.json")
    for path in candidates:
        data = _load_candidate_json(path, {})
        if isinstance(data, dict) and data:
            return data
    return {}


def _bottleneck_activity(celonis: dict[str, Any]) -> dict[str, Any] | None:
    activities = celonis.get("activities")
    if not isinstance(activities, list):
        return None
    for activity in activities:
        if isinstance(activity, dict) and activity.get("bottleneck") is True:
            return activity
    return None


def _duration_hours(activity: dict[str, Any] | None) -> float:
    if not activity:
        return 0.0
    try:
        return float(activity.get("duration_median_hours", activity.get("avg_duration_hours", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _supplier_name(invoice: dict[str, Any], suppliers: dict[str, dict[str, Any]]) -> str | None:
    supplier = suppliers.get(str(invoice.get("supplier_id") or ""))
    return invoice.get("supplier_name") or (supplier or {}).get("name")


def _cycle_time_hours(invoice: dict[str, Any]) -> float:
    try:
        return max(float(invoice.get("cycle_time_hours") or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _process_activities(total_hours: float) -> list[dict[str, Any]]:
    return [
        {
            "activity": item["name"],
            "pct_of_total": item["pct_of_total"],
            "duration_hours": round(total_hours * (item["pct_of_total"] / 100.0), 2),
            "system": item["system"],
        }
        for item in _PROCESS_ACTIVITY_TEMPLATE
    ]


def _process_bottleneck(invoice: dict[str, Any], activities: list[dict[str, Any]]) -> dict[str, Any]:
    bottleneck: dict[str, Any] = max(
        activities,
        key=lambda item: float(item.get("duration_hours") or 0.0),
        default={},
    )
    category = str(invoice.get("category") or "")
    return {
        "activity": bottleneck.get("activity"),
        "duration_hours": bottleneck.get("duration_hours", 0.0),
        "pct_of_total": bottleneck.get("pct_of_total", 0.0),
        "reason": _BOTTLENECK_REASONS.get(category, "Longest fixture activity in the invoice process."),
        "system": bottleneck.get("system"),
    }


@router.get("/fingerprint", response_model=GenericResponse)
def fingerprint(invoice_id: str) -> dict[str, Any]:
    invoice = find_invoice(invoice_id)
    if invoice is None:
        return {"error": f"Invoice {invoice_id} not found"}
    factors = compute_all_factors(invoice)
    dominant_factor = max(factors, key=lambda name: factors[name]) if factors else None
    return {
        "invoice_id": invoice.get("invoice_id", invoice_id),
        "category": invoice.get("category"),
        "factors": factors,
        "dominant_factor": dominant_factor,
    }


@router.get("/similar", response_model=GenericResponse)
def similar(invoice_id: str, limit: int = Query(5, ge=1, le=50)) -> dict[str, Any]:
    invoice = find_invoice(invoice_id)
    if invoice is None:
        return {"invoice_id": invoice_id, "similar": [], "count": 0, "error": f"Invoice {invoice_id} not found"}
    base_factors = compute_all_factors(invoice)
    suppliers = {str(supplier.get("supplier_id")): supplier for supplier in _load_suppliers()}
    matches: list[dict[str, Any]] = []
    for candidate in _load_invoices():
        candidate_id = candidate.get("invoice_id") or candidate.get("event_id")
        if candidate_id == (invoice.get("invoice_id") or invoice_id):
            continue
        candidate_factors = compute_all_factors(candidate)
        distance = math.sqrt(
            sum(
                (base_factors[name] - candidate_factors[name]) ** 2
                for name in S2PDomainConfig.factors
            )
        )
        matches.append(
            {
                "invoice_id": candidate_id,
                "distance": round(float(distance), 6),
                "category": candidate.get("category"),
                "amount": float(candidate.get("amount", 0.0) or 0.0),
                "supplier": _supplier_name(candidate, suppliers),
            }
        )
    matches.sort(key=lambda item: item["distance"])
    return {"invoice_id": invoice.get("invoice_id", invoice_id), "similar": matches[:limit], "count": len(matches)}


@router.get("/process-context/{invoice_id}", response_model=GenericResponse)
def process_context(invoice_id: str) -> dict[str, Any]:
    invoice = find_invoice(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    total_hours = _cycle_time_hours(invoice)
    activities = _process_activities(total_hours)
    bottleneck = _process_bottleneck(invoice, activities)
    return {
        "invoice_id": invoice.get("invoice_id", invoice_id),
        "supplier_id": invoice.get("supplier_id"),
        "category": invoice.get("category"),
        "total_cycle_time_hours": round(total_hours, 2),
        "activities": activities,
        "activity_timeline": activities,
        "bottleneck": bottleneck,
        "source": "fixture",
        "engine": "ci-platform-s2p",
    }


@router.get("/cross-graph", response_model=GenericResponse)
def cross_graph() -> dict[str, Any]:
    suppliers = _load_suppliers()
    celonis = _load_celonis()
    bottleneck = _bottleneck_activity(celonis)
    bottleneck_duration = _duration_hours(bottleneck)
    insights: list[dict[str, Any]] = []
    for supplier in suppliers:
        try:
            exception_rate = float(supplier.get("exception_rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            exception_rate = 0.0
        impact_score = exception_rate * (1.0 + bottleneck_duration / 60.0)
        insights.append(
            {
                "supplier_id": supplier.get("supplier_id"),
                "supplier": supplier.get("name") or supplier.get("supplier_name"),
                "exception_rate": exception_rate,
                "commodity": supplier.get("category"),
                "category": supplier.get("category"),
                "impact_score": round(float(impact_score), 6),
            }
        )
    insights.sort(key=lambda item: item["impact_score"], reverse=True)
    return {
        "insights": insights,
        "count": len(insights),
        "bottleneck_duration": round(float(bottleneck_duration), 4),
        "bottleneck_activity": (bottleneck or {}).get("name") or (bottleneck or {}).get("id"),
    }


@router.get("/process-signals", response_model=GenericResponse)
def process_signals(supplier_id: str | None = None) -> dict[str, Any]:
    celonis = _load_celonis()
    activities = celonis.get("activities") if isinstance(celonis.get("activities"), list) else []
    recommendations = celonis.get("recommendations") if isinstance(celonis.get("recommendations"), list) else []
    return {
        "available": bool(celonis),
        "supplier_id": supplier_id,
        "process_model": celonis.get("process_model"),
        "variant": celonis.get("variant"),
        "activities": activities,
        "recommendations": recommendations,
        "source": celonis.get("source") or ("celonis_cache" if celonis else None),
    }
