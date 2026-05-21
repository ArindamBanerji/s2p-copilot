"""Fixture-backed S2P discovery and disruption recovery endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/s2p/discovery", tags=["s2p-discovery"])


DISCOVERIES: tuple[dict[str, Any], ...] = (
    {
        "discovery_id": "DISC-YANGTZE-PRICE-RISK",
        "title": "Price increase risk at Yangtze Raw Materials",
        "sources": ["Celonis", "D&B", "Commodity index", "S2P invoice exceptions"],
        "correlation_strength": 0.89,
        "impact_estimate": "$420K exposure in 6 weeks",
        "pattern": "financial_stress_commodity_spike_exception_concentration",
        "confidence": 0.89,
        "discovered_at": "2026-05-01T08:00:00Z",
        "recommendation": (
            "Lock pricing with Yangtze Raw Materials and prepare alternate supplier "
            "backup POs before the commodity increase reaches open invoices."
        ),
    },
    {
        "discovery_id": "DISC-CHICAGO-FORMAT-CLUSTER",
        "title": "Chicago invoice format exception concentration",
        "sources": ["S2P invoice exceptions", "ERP vendor master", "AgentEvolver"],
        "correlation_strength": 0.82,
        "impact_estimate": "$180K annual processing cost",
        "pattern": "regional_format_variance_exception_cluster",
        "confidence": 0.82,
        "discovered_at": "2026-05-01T08:05:00Z",
        "recommendation": (
            "Normalize Chicago supplier invoice formats and route the corrected "
            "template through AgentEvolver shadow validation."
        ),
    },
    {
        "discovery_id": "DISC-Q4-MRO-SEASONAL",
        "title": "Seasonal MRO delivery recovery pattern",
        "sources": ["Supplier profiles", "Purchase orders", "Goods receipt timing"],
        "correlation_strength": 0.76,
        "impact_estimate": "2-week buffer Q4 prevents expedited MRO replenishment",
        "pattern": "q4_mro_delivery_buffer_recovery",
        "confidence": 0.76,
        "discovered_at": "2026-05-01T08:10:00Z",
        "recommendation": "Add Q4 safety stock and pre-book MRO replenishment two weeks earlier.",
    },
)


DISRUPTIONS: tuple[dict[str, Any], ...] = (
    {
        "disruption_id": "TARIFF-SHOCK-2025-01",
        "disruption_type": "tariff_shock",
        "occurrence": 1,
        "period": "Jan 2025",
        "recovery_time_days": 90,
        "recovery_cost": 15_000_000,
        "improvement_from_first": 0.0,
        "pattern_reuse": "none",
        "decisions_applied": 0,
    },
    {
        "disruption_id": "TARIFF-SHOCK-2025-09",
        "disruption_type": "tariff_shock",
        "occurrence": 2,
        "period": "Sep 2025",
        "recovery_time_days": 14,
        "recovery_cost": 2_000_000,
        "improvement_from_first": 0.84,
        "pattern_reuse": "verified disruption-response decisions",
        "decisions_applied": 47,
    },
    {
        "disruption_id": "TARIFF-SHOCK-2026-03",
        "disruption_type": "tariff_shock",
        "occurrence": 3,
        "period": "Mar 2026",
        "recovery_time_days": 3,
        "recovery_cost": 500_000,
        "improvement_from_first": 0.97,
        "pattern_reuse": "62 decisions plus 3 promoted variants",
        "decisions_applied": 62,
    },
)


@router.get("/alerts")
def discovery_alerts() -> dict[str, Any]:
    """Return deterministic cross-system discovery examples for the S2P demo."""

    discoveries = sorted(
        (dict(discovery) for discovery in DISCOVERIES),
        key=lambda discovery: float(discovery["correlation_strength"]),
        reverse=True,
    )
    return {
        "discoveries": discoveries,
        "total_discoveries": len(discoveries),
        "sources_connected": 4,
        "highest_impact": "$420K exposure in 6 weeks",
    }


@router.get("/disruptions")
def disruption_recovery() -> dict[str, Any]:
    """Return deterministic tariff-shock recovery history for the S2P demo."""

    disruptions = [dict(disruption) for disruption in DISRUPTIONS]
    baseline_cost = float(disruptions[0]["recovery_cost"])
    cumulative_savings = sum(
        max(0.0, baseline_cost - float(disruption["recovery_cost"]))
        for disruption in disruptions
    )
    avg_improvement_pct = round(
        sum(float(disruption["improvement_from_first"]) for disruption in disruptions)
        / len(disruptions)
        * 100.0,
        1,
    )
    return {
        "disruptions": disruptions,
        "total_disruptions": len(disruptions),
        "cumulative_savings": cumulative_savings,
        "avg_improvement_pct": avg_improvement_pct,
        "learning_narrative": (
            "Each tariff-shock recovery is faster because S2P centroids accumulated "
            "verified disruption-response patterns from prior supplier decisions."
        ),
    }
