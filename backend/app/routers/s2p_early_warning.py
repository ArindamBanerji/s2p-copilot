"""Supplier early-warning endpoints for S2P."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from app.routers.s2p_data_helpers import load_suppliers
from app.services.supplier_profile_accumulator import accumulator

router = APIRouter(prefix="/api/s2p/suppliers", tags=["s2p-early-warning"])


def _load_supplier_profiles() -> list[dict[str, Any]]:
    fixture_by_id = {str(row.get("supplier_id")): row for row in load_suppliers()}
    rows: list[dict[str, Any]] = []
    for profile in accumulator.get_all_profiles():
        data = asdict(profile)
        fixture = fixture_by_id.get(str(profile.supplier_id), {})
        data.update(
            {
                "name": fixture.get("name") or profile.supplier_name,
                "supplier_name": profile.supplier_name or fixture.get("name") or profile.supplier_id,
                "recent_trend": fixture.get("recent_trend"),
                "avg_invoice_amount": _to_float(fixture.get("avg_invoice_amount"), 0.0),
                "payment_terms": fixture.get("payment_terms"),
                "total_invoices": int(_to_float(fixture.get("total_invoices"), profile.invoice_count)),
                "total_exceptions": int(_to_float(fixture.get("total_exceptions"), 0.0)),
                "otif_score": _to_float(fixture.get("otif_score"), profile.otif or 0.0),
            }
        )
        rows.append(data)
    return rows


def _build_trend_signals(profile: dict[str, Any]) -> list[dict[str, Any]]:
    # These signals are deterministic scenario fixtures, not production predictive modeling.
    name = _supplier_name(profile)
    if "Yangtze Raw Materials" in name:
        return [
            _signal("OTIF", 0.84, 0.94, "declining", "warning"),
            _signal("exception_rate", 0.14, 0.08, "declining", "critical"),
            _signal("financial_health", 0.62, 0.82, "declining", "warning"),
        ]
    if "Rhine-Stahl Metals" in name:
        return [
            _signal("OTIF", 0.86, 0.92, "declining", "watch"),
            _signal("exception_rate", 0.11, 0.08, "declining", "warning"),
            _signal("financial_health", 0.76, 0.82, "stable", "watch"),
        ]

    exception_rate = _clamp01(_to_float(profile.get("exception_rate"), 0.0))
    otif = _clamp01(_to_float(profile.get("otif", profile.get("otif_score")), 0.9))
    total_invoices = max(int(_to_float(profile.get("total_invoices"), profile.get("invoice_count") or 0)), 1)
    total_exceptions = int(_to_float(profile.get("total_exceptions"), exception_rate * total_invoices))
    exception_volume = _clamp01(total_exceptions / max(total_invoices, 1))
    trend = str(profile.get("recent_trend") or profile.get("trend_direction") or "stable")

    signals = [
        _signal(
            "OTIF",
            otif,
            0.92,
            "declining" if otif < 0.88 else "stable",
            "warning" if otif < 0.86 else "watch" if otif < 0.9 else "normal",
        ),
        _signal(
            "exception_rate",
            exception_rate,
            0.08,
            "declining" if exception_rate > 0.1 or trend == "declining" else "stable",
            "warning" if exception_rate > 0.1 else "watch" if exception_rate > 0.08 else "normal",
        ),
        _signal(
            "exception_volume",
            exception_volume,
            0.08,
            "declining" if exception_volume > 0.1 else "stable",
            "watch" if exception_volume > 0.08 else "normal",
        ),
    ]
    return signals


def _risk_score(signals: list[dict[str, Any]]) -> float:
    weights = {"normal": 0.0, "watch": 0.35, "warning": 0.65, "critical": 0.9}
    if not signals:
        return 0.0
    return _clamp01(round(sum(weights.get(str(signal.get("severity")), 0.0) for signal in signals) / len(signals), 2))


def _confidence(signals: list[dict[str, Any]]) -> float:
    if not signals:
        return 0.0
    actionable = sum(1 for signal in signals if signal.get("severity") != "normal")
    return _clamp01(round(0.55 + 0.1 * actionable, 2))


def _pattern(signals: list[dict[str, Any]]) -> str:
    severe = {str(signal.get("signal_name")) for signal in signals if signal.get("severity") in {"warning", "critical"}}
    if {"OTIF", "exception_rate", "financial_health"} <= severe:
        return "financial_stress_delivery_failure"
    if "OTIF" in severe:
        return "otif_erosion"
    if "exception_rate" in severe:
        return "exception_acceleration"
    return "watchlist"


def _recommendation(risk_score: float, pattern: str) -> str:
    if pattern == "financial_stress_delivery_failure":
        return "Qualify backup supplier and review payment exposure before the next renewal cycle."
    if pattern == "otif_erosion":
        return "Monitor OTIF erosion and request recovery plan from supplier owner."
    if risk_score > 0.5:
        return "Review supplier controls and prepare mitigation options."
    return "Monitor supplier signals during weekly exception review."


def _lead_time_weeks(risk_score: float, pattern: str) -> int:
    if pattern == "financial_stress_delivery_failure":
        return 6
    if pattern == "otif_erosion":
        return 12
    if risk_score >= 0.6:
        return 8
    return 16


@router.get("/early-warnings")
def early_warnings() -> dict[str, Any]:
    profiles = _load_supplier_profiles()
    warnings = [_warning_payload(profile) for profile in profiles]
    active = [warning for warning in warnings if warning["risk_score"] > 0.30]
    active.sort(key=lambda warning: (-float(warning["risk_score"]), str(warning["supplier_id"])))
    return {
        "warnings": active,
        "monitored_suppliers": len(profiles),
        "active_warnings": len(active),
        "patterns_detected": len({warning["pattern"] for warning in active}),
    }


@router.get("/trend-signals")
def trend_signals(supplier_id: str) -> dict[str, Any]:
    profile = next(
        (row for row in _load_supplier_profiles() if str(row.get("supplier_id")) == supplier_id),
        None,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    return {"supplier_id": supplier_id, "signals": _build_trend_signals(profile)}


def _warning_payload(profile: dict[str, Any]) -> dict[str, Any]:
    signals = _build_trend_signals(profile)
    score = _demo_risk_override(profile, _risk_score(signals))
    pattern = _demo_pattern_override(profile, _pattern(signals))
    confidence = _demo_confidence_override(profile, _confidence(signals))
    return {
        "supplier_id": str(profile.get("supplier_id")),
        "supplier_name": _supplier_name(profile),
        "risk_score": score,
        "confidence": confidence,
        "signals": signals,
        "pattern": pattern,
        "recommendation": _recommendation(score, pattern),
        "lead_time_weeks": _lead_time_weeks(score, pattern),
    }


def _signal(
    signal_name: str,
    current_value: float,
    baseline_value: float,
    direction: str,
    severity: str,
) -> dict[str, Any]:
    delta_pct = 0.0
    if baseline_value:
        delta_pct = ((current_value - baseline_value) / baseline_value) * 100.0
    return {
        "signal_name": signal_name,
        "current_value": round(float(current_value), 4),
        "baseline_value": round(float(baseline_value), 4),
        "delta_pct": round(delta_pct, 2),
        "direction": direction,
        "severity": severity,
    }


def _demo_risk_override(profile: dict[str, Any], computed: float) -> float:
    name = _supplier_name(profile)
    if "Yangtze Raw Materials" in name:
        return 0.78
    if "Rhine-Stahl Metals" in name:
        return 0.45
    return computed


def _demo_confidence_override(profile: dict[str, Any], computed: float) -> float:
    name = _supplier_name(profile)
    if "Yangtze Raw Materials" in name:
        return 0.78
    if "Rhine-Stahl Metals" in name:
        return 0.65
    return computed


def _demo_pattern_override(profile: dict[str, Any], computed: str) -> str:
    name = _supplier_name(profile)
    if "Yangtze Raw Materials" in name:
        return "financial_stress_delivery_failure"
    if "Rhine-Stahl Metals" in name:
        return "otif_erosion"
    return computed


def _supplier_name(profile: dict[str, Any]) -> str:
    return str(profile.get("supplier_name") or profile.get("name") or profile.get("supplier_id") or "")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
