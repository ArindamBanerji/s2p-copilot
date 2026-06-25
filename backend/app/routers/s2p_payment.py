"""Supplier payment strategy endpoints for S2P."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.responses import GenericResponse
from app.routers.s2p_data_helpers import load_suppliers
from app.services.supplier_profile_accumulator import accumulator

router = APIRouter(prefix="/api/s2p/suppliers", tags=["s2p-payment"])

STRATEGY_ORDER = {"early_pay": 0, "on_time": 1, "extend": 2}
VALID_STRATEGIES = set(STRATEGY_ORDER)
COST_OF_CAPITAL = 0.05
STANDARD_DPO_IMPACT_DAYS = 8.0

# Deterministic scenario fixtures for F19. These are demo heuristics, not
# production financial optimization or a learned DPO model.
DEMO_PAYMENT_BEHAVIOR = {
    "Aster": {
        "recommended_strategy": "early_pay",
        "payment_otif_correlation": 0.72,
        "discount_opportunity": 180_000.0,
        "risk_if_delayed": "high",
        "confidence": 0.86,
        "reason": "Early pay improves OTIF for exception-prone chemical supply and unlocks a supplier discount.",
    },
    "Yangtze": {
        "recommended_strategy": "early_pay",
        "payment_otif_correlation": 0.65,
        "discount_opportunity": 120_000.0,
        "risk_if_delayed": "high",
        "confidence": 0.84,
        "reason": "Supplier deprioritizes orders when payment exceeds 45 days; early pay protects raw material continuity.",
    },
    "Northstar": {
        "recommended_strategy": "on_time",
        "payment_otif_correlation": 0.15,
        "discount_opportunity": 0.0,
        "risk_if_delayed": "low",
        "confidence": 0.78,
        "reason": "No meaningful payment-performance correlation; already performs well on Net-30 terms.",
    },
    "Novatek": {
        "recommended_strategy": "on_time",
        "payment_otif_correlation": 0.08,
        "discount_opportunity": 0.0,
        "risk_if_delayed": "low",
        "confidence": 0.76,
        "reason": "Premium service levels remain stable regardless of payment timing.",
    },
    "Pacifica": {
        "recommended_strategy": "on_time",
        "payment_otif_correlation": 0.22,
        "discount_opportunity": 0.0,
        "risk_if_delayed": "medium",
        "confidence": 0.74,
        "reason": "Stable performance across terms; keep standard on-time payment cadence.",
    },
    "Meridian": {
        "recommended_strategy": "extend",
        "payment_otif_correlation": -0.02,
        "discount_opportunity": 0.0,
        "risk_if_delayed": "low",
        "confidence": 0.82,
        "reason": "No performance impact from later payment; extend terms for DPO +8 days.",
    },
    "Boreal": {
        "recommended_strategy": "extend",
        "payment_otif_correlation": 0.05,
        "discount_opportunity": 0.0,
        "risk_if_delayed": "low",
        "confidence": 0.75,
        "reason": "No payment-performance correlation and lower volume support a working-capital extension.",
    },
    "Gridline": {
        "recommended_strategy": "extend",
        "payment_otif_correlation": -0.01,
        "discount_opportunity": 0.0,
        "risk_if_delayed": "low",
        "confidence": 0.8,
        "reason": "Utility service has no OTIF sensitivity to payment timing; extend where contract allows.",
    },
    "Rhine-Stahl": {
        "recommended_strategy": "early_pay",
        "payment_otif_correlation": 0.58,
        "discount_opportunity": 40_000.0,
        "risk_if_delayed": "medium",
        "confidence": 0.81,
        "reason": "Quality improves with early payment and the supplier offers limited discount upside.",
    },
    "Helix": {
        "recommended_strategy": "on_time",
        "payment_otif_correlation": 0.18,
        "discount_opportunity": 0.0,
        "risk_if_delayed": "low",
        "confidence": 0.77,
        "reason": "Stable lab supplier performance supports normal on-time payment.",
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
                "supplier_id": str(profile.supplier_id),
                "supplier_name": profile.supplier_name or fixture.get("name") or profile.supplier_id,
                "name": fixture.get("name") or profile.supplier_name,
                "category": fixture.get("category") or (profile.categories[0] if profile.categories else None),
                "current_terms": fixture.get("current_terms") or fixture.get("payment_terms") or "Unknown",
                "payment_terms": fixture.get("payment_terms"),
                "avg_invoice_amount": _to_float(fixture.get("avg_invoice_amount"), 0.0),
                "total_invoices": int(_to_float(fixture.get("total_invoices"), profile.invoice_count)),
                "total_exceptions": int(_to_float(fixture.get("total_exceptions"), 0.0)),
                "otif_score": _to_float(fixture.get("otif_score"), profile.otif or 0.0),
                "recent_trend": fixture.get("recent_trend"),
            }
        )
        rows.append(data)
    return rows


def _build_payment_behaviors(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [_payment_behavior_for_supplier(profile) for profile in profiles],
        key=lambda row: (
            STRATEGY_ORDER[row["recommended_strategy"]],
            -float(row["discount_opportunity"]) if row["recommended_strategy"] == "early_pay" else 0.0,
            str(row["supplier_name"]),
        ),
    )


def _payment_behavior_for_supplier(profile: dict[str, Any]) -> dict[str, Any]:
    behavior = _demo_behavior(profile)
    strategy = str(behavior["recommended_strategy"])
    if strategy not in VALID_STRATEGIES:
        strategy = _strategy_from_rule(behavior)
    annual_spend = _annual_spend(profile)
    discount_opportunity = max(0.0, float(behavior["discount_opportunity"]))
    dpo_impact = STANDARD_DPO_IMPACT_DAYS if strategy == "extend" else -20.0 if strategy == "early_pay" else 0.0
    cash_flow_benefit = compute_cash_flow_benefit(annual_spend, dpo_impact)
    annualized_return = compute_early_pay_value(profile, annual_spend)["annualized_return_pct"] if strategy == "early_pay" else 0.0
    correlation = _clamp(float(behavior["payment_otif_correlation"]), -1.0, 1.0)
    return {
        "supplier_id": str(profile.get("supplier_id")),
        "supplier_name": _supplier_name(profile),
        "current_terms": str(profile.get("current_terms") or profile.get("payment_terms") or "Unknown"),
        "recommended_strategy": strategy,
        "strategy": strategy,
        "reason": str(behavior["reason"]),
        "payment_otif_correlation": correlation,
        "otif_correlation": correlation,
        "discount_opportunity": discount_opportunity,
        "discount_captured": discount_opportunity,
        "annualized_return_pct": annualized_return,
        "dpo_impact_days": dpo_impact,
        "cash_flow_benefit": cash_flow_benefit,
        "risk_if_delayed": str(behavior["risk_if_delayed"]),
        "confidence": _clamp01(float(behavior["confidence"])),
        "annual_spend": annual_spend,
        "narrative": _strategy_narrative(
            _supplier_name(profile),
            strategy,
            discount_opportunity,
            annualized_return,
            correlation,
            dpo_impact,
            cash_flow_benefit,
            annual_spend,
        ),
    }


def _strategy_counts(strategies: list[dict[str, Any]]) -> dict[str, int]:
    return {
        strategy: sum(1 for row in strategies if row["recommended_strategy"] == strategy)
        for strategy in ("early_pay", "on_time", "extend")
    }


def _summary(strategies: list[dict[str, Any]], total_discount: float, dpo_days: float) -> str:
    counts = _strategy_counts(strategies)
    return (
        f"{counts['early_pay']} early-pay (${total_discount / 1000:.0f}K/yr), "
        f"{counts['on_time']} on-time, {counts['extend']} extend (+{dpo_days:.0f} DPO days)"
    )


def compute_payment_otif_correlation(supplier_id: str, history: list[dict[str, Any]]) -> float:
    """Pearson correlation between payment days and subsequent OTIF."""
    pairs = [
        (float(row["payment_days"]), float(row["otif"]))
        for row in history
        if row.get("supplier_id") == supplier_id
        and row.get("payment_days") is not None
        and row.get("otif") is not None
    ]
    if len(pairs) < 2:
        return 0.0
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    x_var = sum((x - x_mean) ** 2 for x in xs)
    y_var = sum((y - y_mean) ** 2 for y in ys)
    if x_var <= 0 or y_var <= 0:
        return 0.0
    return round(_clamp(numerator / ((x_var * y_var) ** 0.5), -1.0, 1.0), 2)


def compute_dpo_portfolio(strategies: list[dict[str, Any]]) -> dict[str, float]:
    """Weighted DPO and cash-flow summary across recommended strategies."""
    total_spend = sum(float(row.get("annual_spend") or 0.0) for row in strategies)
    if total_spend <= 0:
        return {"portfolio_dpo_improvement": 0.0, "cash_flow_benefit": 0.0}
    weighted_days = sum(
        float(row.get("annual_spend") or 0.0) * float(row.get("dpo_impact_days") or 0.0)
        for row in strategies
    ) / total_spend
    cash_flow = sum(float(row.get("cash_flow_benefit") or 0.0) for row in strategies)
    return {
        "portfolio_dpo_improvement": round(weighted_days, 1),
        "cash_flow_benefit": round(cash_flow, 2),
    }


def compute_early_pay_value(supplier: dict[str, Any], annual_spend: float) -> dict[str, float]:
    """2/10 net 30 early-pay value."""
    capture_rate = float(supplier.get("capture_rate", 1.0) or 1.0)
    discount_captured = max(0.0, annual_spend * 0.02 * capture_rate)
    annualized_return = 36.7
    return {
        "discount_captured": round(discount_captured, 2),
        "annualized_return_pct": round(annualized_return, 1),
    }


def compute_cash_flow_benefit(annual_spend: float, dpo_impact_days: float) -> float:
    if dpo_impact_days <= 0:
        return 0.0
    return round(annual_spend * (dpo_impact_days / 365.0) * COST_OF_CAPITAL, 2)


@router.get("/payment-strategy", response_model=GenericResponse)
def payment_strategy() -> dict[str, Any]:
    strategies = _build_payment_behaviors(_load_supplier_profiles())
    total_discount = sum(float(row["discount_opportunity"]) for row in strategies)
    dpo_days = STANDARD_DPO_IMPACT_DAYS if any(row["recommended_strategy"] == "extend" for row in strategies) else 0.0
    portfolio = compute_dpo_portfolio(strategies)
    total_annual_benefit = total_discount + portfolio["cash_flow_benefit"]
    return {
        "strategies": strategies,
        "total_discount_opportunity": total_discount,
        "suppliers_analyzed": len(strategies),
        "dpo_improvement_days": dpo_days,
        "portfolio_dpo_improvement": portfolio["portfolio_dpo_improvement"],
        "cash_flow_benefit": portfolio["cash_flow_benefit"],
        "total_annual_benefit": round(total_annual_benefit, 2),
        "summary": _summary(strategies, total_discount, dpo_days),
        "narrative": (
            f"Portfolio optimization: DPO {portfolio['portfolio_dpo_improvement']:+.1f} days. "
            f"Total annual benefit: ${total_annual_benefit / 1_000_000:.2f}M."
        ),
    }


@router.get("/payment-portfolio", response_model=GenericResponse)
def payment_portfolio() -> dict[str, Any]:
    strategies = _build_payment_behaviors(_load_supplier_profiles())
    portfolio = compute_dpo_portfolio(strategies)
    total_discount = sum(float(row["discount_opportunity"]) for row in strategies)
    total_annual_benefit = total_discount + portfolio["cash_flow_benefit"]
    return {
        "portfolio_dpo_improvement": portfolio["portfolio_dpo_improvement"],
        "cash_flow_benefit": portfolio["cash_flow_benefit"],
        "discount_opportunity": total_discount,
        "total_annual_benefit": round(total_annual_benefit, 2),
        "strategy_counts": _strategy_counts(strategies),
        "narrative": (
            f"Portfolio optimization: DPO {portfolio['portfolio_dpo_improvement']:+.1f} days. "
            f"Total annual benefit: ${total_annual_benefit / 1_000_000:.2f}M."
        ),
    }


@router.get("/payment-behavior", response_model=GenericResponse)
def payment_behavior(supplier_id: str) -> dict[str, Any]:
    profile = next(
        (row for row in _load_supplier_profiles() if str(row.get("supplier_id")) == supplier_id),
        None,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    return _payment_behavior_for_supplier(profile)


def _demo_behavior(profile: dict[str, Any]) -> dict[str, Any]:
    name = _supplier_name(profile)
    for marker, behavior in DEMO_PAYMENT_BEHAVIOR.items():
        if marker.lower() in name.lower():
            return dict(behavior)

    correlation = _fallback_correlation(profile)
    discount = 0.0
    strategy = _strategy_from_rule(
        {
            "payment_otif_correlation": correlation,
            "discount_opportunity": discount,
            "risk_if_delayed": "low",
        }
    )
    return {
        "recommended_strategy": strategy,
        "payment_otif_correlation": correlation,
        "discount_opportunity": discount,
        "risk_if_delayed": "low",
        "confidence": 0.55,
        "reason": "Fallback payment behavior derived from fixture payment terms and supplier reliability.",
    }


def _strategy_from_rule(behavior: dict[str, Any]) -> str:
    correlation = float(behavior.get("payment_otif_correlation") or 0.0)
    discount = float(behavior.get("discount_opportunity") or 0.0)
    risk = str(behavior.get("risk_if_delayed") or "low")
    if correlation > 0.5 and discount > 0.0:
        return "early_pay"
    if correlation < 0.1 and risk == "low":
        return "extend"
    return "on_time"


def _fallback_correlation(profile: dict[str, Any]) -> float:
    exception_rate = _to_float(profile.get("exception_rate"), 0.0)
    otif = _to_float(profile.get("otif", profile.get("otif_score")), 0.9)
    return round(_clamp((exception_rate * 1.5) + ((0.9 - otif) * 0.5), -1.0, 1.0), 2)


def _supplier_name(profile: dict[str, Any]) -> str:
    return str(profile.get("supplier_name") or profile.get("name") or profile.get("supplier_id") or "")


def _annual_spend(profile: dict[str, Any]) -> float:
    invoices = max(1, int(_to_float(profile.get("total_invoices"), 1)))
    amount = _to_float(profile.get("avg_invoice_amount"), 0.0)
    return round(invoices * amount, 2)


def _strategy_narrative(
    supplier_name: str,
    strategy: str,
    discount: float,
    annualized_return: float,
    correlation: float,
    dpo_impact: float,
    cash_flow_benefit: float,
    annual_spend: float,
) -> str:
    if strategy == "early_pay":
        return (
            f"Early-pay {supplier_name}: captures ${discount:,.0f}/year. "
            f"Annualized return: {annualized_return:.1f}%."
        )
    if strategy == "extend":
        return (
            f"Extend {supplier_name}: no material delivery impact observed. "
            f"DPO {dpo_impact:+.0f} days. Cash flow benefit: ${cash_flow_benefit:,.0f}/year."
        )
    return (
        f"On-time {supplier_name}: payment timing correlates with delivery performance "
        f"(r={correlation:.2f}) on ${annual_spend:,.0f} annual spend."
    )


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)
