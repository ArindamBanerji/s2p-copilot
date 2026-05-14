"""S2P process value graph endpoints for financial impact and leakage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from app.domains.s2p.factors import compute_all_factors
from app.routers.s2p_data_helpers import load_invoices

router = APIRouter(prefix="/api/s2p/pvg", tags=["s2p-pvg"])

ANNUAL_TARGET_USD = 680000.0
BREAKDOWN = {
    "leakage_prevented": 45.0,
    "cycle_time_saved": 30.0,
    "auto_approve_efficiency": 25.0,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _workspace_root() -> Path:
    return _repo_root().parent


def _data_path(filename: str) -> Path:
    return _repo_root() / "data" / filename


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _load_process_data() -> dict[str, Any]:
    candidates = [
        _data_path("celonis_process_data.json"),
        _workspace_root() / "copilot-sdk" / "apps" / "dataops" / "backend" / "data" / "celonis_process_data.json",
    ]
    for path in candidates:
        data = _load_json(path, {})
        if isinstance(data, dict) and data:
            return data
    return {}


def _duration_minutes(activity: dict[str, Any]) -> float:
    if activity.get("duration_median_min") is not None:
        return float(activity.get("duration_median_min") or 0.0)
    if activity.get("duration_minutes") is not None:
        return float(activity.get("duration_minutes") or 0.0)
    return float(activity.get("avg_duration_hours") or 0.0) * 60.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return float((ordered[midpoint - 1] + ordered[midpoint]) / 2.0)


def _process_activities(process_data: dict[str, Any]) -> list[dict[str, Any]]:
    activities = process_data.get("activities")
    return [activity for activity in activities if isinstance(activity, dict)] if isinstance(activities, list) else []


@router.get("/variants")
def variants() -> dict[str, Any]:
    process_data = _load_process_data()
    activities = _process_activities(process_data)
    if process_data and activities:
        durations = [_duration_minutes(activity) for activity in activities]
        median_cycle = _median(durations)
        return {
            "variants": [
                {
                    "variant_id": str(process_data.get("variant") or "standard").lower().replace(" ", "-"),
                    "variant_name": process_data.get("variant") or "Standard process",
                    "volume": int(process_data.get("total_cases") or process_data.get("variant_frequency") or 0),
                    "median_cycle_minutes": round(float(median_cycle), 2),
                    "process_model": process_data.get("process_model"),
                    "source": process_data.get("source") or "celonis_cache",
                }
            ],
            "count": 1,
            "source": process_data.get("source") or "celonis_cache",
        }

    invoices = load_invoices()
    by_category: dict[str, int] = {}
    for invoice in invoices:
        category = str(invoice.get("category") or "uncategorized")
        by_category[category] = by_category.get(category, 0) + 1
    fallback = [
        {
            "variant_id": f"invoice-{category}".replace("_", "-"),
            "variant_name": category.replace("_", " ").title(),
            "volume": count,
            "median_cycle_minutes": 0.0,
            "process_model": None,
            "source": "synthetic_invoices.json",
        }
        for category, count in sorted(by_category.items())
    ]
    return {"variants": fallback, "count": len(fallback), "source": "synthetic_invoices.json"}


@router.get("/impact")
def impact(period: str = Query("annual", pattern="^(monthly|quarterly|annual)$")) -> dict[str, Any]:
    scale = {"monthly": 1 / 12, "quarterly": 1 / 4, "annual": 1}[period]
    total = round(ANNUAL_TARGET_USD * scale, 2)
    breakdown = {
        name: {"amount": round(total * pct / 100.0, 2), "pct": pct}
        for name, pct in BREAKDOWN.items()
    }
    return {
        "period": period,
        "annual_target": int(ANNUAL_TARGET_USD),
        "annual_target_usd": int(ANNUAL_TARGET_USD),
        "total_savings": total,
        "total_savings_usd": total,
        "breakdown": breakdown,
    }


@router.get("/leakage")
def leakage() -> dict[str, Any]:
    flagged: list[dict[str, Any]] = []
    for invoice in load_invoices():
        factors = compute_all_factors(invoice)
        variance = float(factors.get("amount_variance_ratio", 0.0))
        correlation = float(factors.get("commodity_index_correlation", 1.0))
        if variance > 0.15 and correlation < 0.5:
            amount = float(invoice.get("amount") or 0.0)
            at_risk = round(amount * variance, 2)
            flagged.append(
                {
                    "invoice_id": invoice.get("invoice_id") or invoice.get("event_id"),
                    "supplier_id": invoice.get("supplier_id"),
                    "supplier_name": invoice.get("supplier_name"),
                    "amount": amount,
                    "amount_variance_ratio": variance,
                    "commodity_index_correlation": correlation,
                    "at_risk_amount": at_risk,
                    "at_risk_usd": at_risk,
                }
            )
    flagged.sort(key=lambda item: item["at_risk_amount"], reverse=True)
    total_at_risk = round(sum(item["at_risk_amount"] for item in flagged), 2)
    return {
        "flagged_invoices": flagged,
        "items": flagged,
        "total_at_risk": total_at_risk,
        "estimated_leakage_usd": total_at_risk,
        "count": len(flagged),
        "flagged_count": len(flagged),
        "rule": "amount_variance_ratio > 0.15 and commodity_index_correlation < 0.5",
    }


@router.get("/cycle-time")
def cycle_time() -> dict[str, Any]:
    process_data = _load_process_data()
    activities = _process_activities(process_data)
    if not process_data or not activities:
        return {
            "available": False,
            "reason": "Celonis data not configured",
            "activities": [],
        }

    shaped = [
        {
            "id": activity.get("id") or activity.get("activity_id"),
            "name": activity.get("name") or activity.get("activity"),
            "duration_minutes": round(_duration_minutes(activity), 2),
            "is_bottleneck": bool(activity.get("bottleneck")),
            "status": activity.get("status"),
            "system": activity.get("system"),
        }
        for activity in activities
    ]
    total = round(sum(activity["duration_minutes"] for activity in shaped), 2)
    bottleneck = next((activity for activity in shaped if activity["is_bottleneck"]), None)
    if bottleneck is None and shaped:
        bottleneck = max(shaped, key=lambda item: item["duration_minutes"])
    bottleneck_minutes = float((bottleneck or {}).get("duration_minutes") or 0.0)
    return {
        "available": True,
        "process_model": process_data.get("process_model"),
        "variant": process_data.get("variant"),
        "activities": shaped,
        "total_median_minutes": total,
        "bottleneck_name": (bottleneck or {}).get("name"),
        "bottleneck_activity": (bottleneck or {}).get("name"),
        "bottleneck_pct": round(bottleneck_minutes / total, 4) if total else 0.0,
        "source": process_data.get("source") or "celonis_cache",
    }
