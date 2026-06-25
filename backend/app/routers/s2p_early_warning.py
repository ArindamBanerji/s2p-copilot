"""Supplier early-warning endpoints for S2P."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, cast

from fastapi import APIRouter, HTTPException

from app.models.responses import GenericResponse
from app.routers.s2p_data_helpers import load_suppliers
from app.services.supplier_profile_accumulator import accumulator

router = APIRouter(prefix="/api/s2p/suppliers", tags=["s2p-early-warning"])

DISTRESS_PATTERNS = {
    "financial_stress": {
        "signals": {
            "otif": "declining",
            "pricing": "increasing",
            "exception_rate": "increasing",
        },
        "description": "Pattern consistent with financial stress leading to delivery failure",
        "typical_days_to_impact": 45,
        "example_supplier": "Yangtze Raw Materials",
    },
    "operational_degradation": {
        "signals": {"otif": "declining", "exception_rate": "increasing"},
        "description": "Operational capacity deterioration",
        "typical_days_to_impact": 30,
        "example_supplier": "Rhine-Stahl Metals",
    },
    "market_pressure": {
        "signals": {"pricing": "increasing"},
        "description": "Market-driven cost pressure",
        "typical_days_to_impact": 60,
        "example_supplier": "Aster Industrial Chemicals",
    },
}


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
                "quarterly_otif": fixture.get("quarterly_otif") or {},
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


def compute_combined_severity(trends: list[dict[str, Any]]) -> float:
    """Combined severity from simultaneous supplier deterioration signals."""
    if not trends:
        return 0.0
    max_signals = 3
    declining = [trend for trend in trends if _is_declining_signal(trend)]
    if not declining:
        return 0.0
    total_decline = sum(abs(float(trend.get("delta_pct") or 0.0)) / 100.0 for trend in declining)
    return _clamp01(round(total_decline * (len(declining) / max_signals), 3))


def match_pattern(trends: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match declining supplier signals against known distress patterns."""
    signal_state = {_canonical_signal_name(trend): trend for trend in trends if _is_declining_signal(trend)}
    names = set(signal_state)
    if {"otif", "exception_rate", "pricing"} <= names or {"otif", "exception_rate", "financial_health"} <= names:
        pattern_name = "financial_stress"
    elif {"otif", "exception_rate"} <= names:
        pattern_name = "operational_degradation"
    elif "pricing" in names:
        pattern_name = "market_pressure"
    else:
        return None

    pattern = DISTRESS_PATTERNS[pattern_name]
    severity = compute_combined_severity(trends)
    return {
        "pattern": pattern_name,
        "description": pattern["description"],
        "days_to_impact": pattern["typical_days_to_impact"],
        "confidence": _confidence_from_severity(severity, len(names)),
    }


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


@router.get("/early-warnings/patterns", response_model=GenericResponse)
def early_warning_patterns() -> dict[str, Any]:
    return {
        "patterns": [
            {
                "name": name,
                "signals": definition["signals"],
                "description": definition["description"],
                "typical_days_to_impact": definition["typical_days_to_impact"],
                "example_supplier": definition["example_supplier"],
            }
            for name, definition in DISTRESS_PATTERNS.items()
        ]
    }


@router.get("/early-warnings", response_model=GenericResponse)
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


@router.get("/trends", response_model=GenericResponse)
def supplier_trends() -> dict[str, Any]:
    profiles = _load_supplier_profiles()
    trends = []
    supplier_rows = []
    for profile in profiles:
        raw_quarterly = profile.get("quarterly_otif")
        quarterly: dict[str, Any] = (
            cast(dict[str, Any], raw_quarterly)
            if isinstance(raw_quarterly, dict)
            else {}
        )
        values = [float(value) for value in quarterly.values()]
        delta = round(values[-1] - values[0], 4) if len(values) >= 2 else 0.0
        direction = "declining" if delta < -0.10 else "improving" if delta > 0.05 else "stable"
        signals = _build_trend_signals(profile)
        quarterly_series = [
            {"quarter": quarter, "otif": float(otif)}
            for quarter, otif in quarterly.items()
        ]
        trends.append(
            {
                "supplier_id": str(profile.get("supplier_id")),
                "supplier_name": _supplier_name(profile),
                "quarterly_otif": quarterly,
                "otif_delta": delta,
                "direction": direction,
                "risk_score": _demo_risk_override(profile, _risk_score(signals)),
                "signals": signals,
            }
        )
        supplier_rows.append(
            {
                "supplier_id": str(profile.get("supplier_id")),
                "name": _supplier_name(profile),
                "quarterly_otif": quarterly_series,
                "trend": direction,
                "trend_delta": delta,
            }
        )
    trends.sort(key=lambda item: (str(item["direction"] != "declining"), str(item["supplier_id"])))
    supplier_rows.sort(key=lambda item: (str(item["trend"] != "declining"), str(item["supplier_id"])))
    return {
        "suppliers": supplier_rows,
        "trends": trends,
        "total": len(trends),
        "declining_count": sum(1 for item in trends if item["direction"] == "declining"),
        "improving_count": sum(1 for item in trends if item["direction"] == "improving"),
        "source": "fixture",
    }


@router.get("/trend-signals", response_model=GenericResponse)
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
    pattern_match = match_pattern(signals)
    combined_severity = compute_combined_severity(signals)
    confidence = _demo_confidence_override(
        profile,
        pattern_match["confidence"] if pattern_match else _confidence(signals),
    )
    days_to_impact = (
        int(pattern_match["days_to_impact"])
        if pattern_match
        else _lead_time_weeks(score, pattern) * 7
    )
    return {
        "supplier_id": str(profile.get("supplier_id")),
        "supplier_name": _supplier_name(profile),
        "risk_score": score,
        "confidence": confidence,
        "signals": signals,
        "declining_signals": [_canonical_signal_name(signal) for signal in signals if _is_declining_signal(signal)],
        "pattern": pattern,
        "pattern_match": pattern_match["pattern"] if pattern_match else None,
        "combined_severity": combined_severity,
        "days_to_impact": days_to_impact,
        "recommendation": _recommendation(score, pattern),
        "lead_time_weeks": _lead_time_weeks(score, pattern),
        "narrative": _warning_narrative(profile, signals, pattern_match, days_to_impact),
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


def _warning_narrative(
    profile: dict[str, Any],
    signals: list[dict[str, Any]],
    pattern_match: dict[str, Any] | None,
    days_to_impact: int | None,
) -> str:
    name = _supplier_name(profile)
    declining = [signal for signal in signals if _is_declining_signal(signal)]
    if pattern_match:
        description = str(pattern_match["description"])
        action = "Qualify backup supplier now." if pattern_match["pattern"] == "financial_stress" else "Review mitigation plan."
        return (
            f"Supplier {name}: {len(declining)} signals declining simultaneously. "
            f"{_signal_summary(declining)} {description}. "
            f"Estimated impact window: {days_to_impact} days. {action}"
        )
    return f"Supplier {name}: signals remain below combined deterioration threshold. Continue weekly monitoring."


def _signal_summary(signals: list[dict[str, Any]]) -> str:
    parts = []
    for signal in signals[:3]:
        name = str(signal.get("signal_name"))
        current = signal.get("current_value")
        baseline = signal.get("baseline_value")
        parts.append(f"{name} moved from {baseline} to {current}.")
    return " ".join(parts)


def _is_declining_signal(signal: dict[str, Any]) -> bool:
    name = _canonical_signal_name(signal)
    direction = str(signal.get("direction") or "").lower()
    delta = float(signal.get("delta_pct") or 0.0)
    if name in {"exception_rate", "pricing"}:
        return direction in {"declining", "increasing"} and delta > 0
    return direction == "declining" or delta < 0


def _canonical_signal_name(signal: dict[str, Any]) -> str:
    name = str(signal.get("signal_name") or "").lower()
    mapping = {
        "otif": "otif",
        "exception_rate": "exception_rate",
        "exception_volume": "exception_rate",
        "financial_health": "financial_health",
        "pricing": "pricing",
        "price": "pricing",
    }
    return mapping.get(name, name)


def _confidence_from_severity(severity: float, signal_count: int) -> float:
    return _clamp01(round(0.55 + severity * 0.5 + min(signal_count, 3) * 0.06, 2))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
