"""Fixture-backed S2P discovery and disruption recovery endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from copilot_sdk.state.cached_static import cached_static

from app.models.responses import GenericResponse

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


EXTENDED_DISCOVERIES: tuple[dict[str, Any], ...] = (
    {
        "discovery_id": "DISC-EXT-001",
        "title": "Yangtze tariff pressure propagates to invoice holds",
        "type": "commodity_risk",
        "sources": ["Celonis", "D&B", "Commodity index", "S2P invoice exceptions"],
        "correlation_strength": 0.91,
        "confidence": 0.89,
        "impact_estimate": "$520K exposure in 8 weeks",
        "supplier_ids": ["SUP-YANGTZE", "SUP-ALT-SEA"],
        "pattern": "commodity_spike_supplier_stress_exception_cluster",
        "first_detected": "2026-05-02T09:00:00Z",
        "detection_count": 18,
        "recommendation": "Lock tariff-exposed pricing and pre-approve alternate suppliers.",
        "propagation_path": ["commodity_index", "supplier_risk", "price_variance", "buyer_escalation"],
    },
    {
        "discovery_id": "DISC-EXT-002",
        "title": "Chicago format variance drives duplicate review loops",
        "type": "format_compliance",
        "sources": ["ERP vendor master", "S2P invoice exceptions", "AgentEvolver"],
        "correlation_strength": 0.87,
        "confidence": 0.84,
        "impact_estimate": "$210K processing cost",
        "supplier_ids": ["SUP-CHI-001", "SUP-CHI-004", "SUP-CHI-012"],
        "pattern": "regional_format_variance_duplicate_review",
        "first_detected": "2026-05-02T10:15:00Z",
        "detection_count": 14,
        "recommendation": "Normalize Chicago supplier templates and shadow-test the revised rule.",
        "propagation_path": ["invoice_format", "duplicate_score", "hold_for_review", "rule_template"],
    },
    {
        "discovery_id": "DISC-EXT-003",
        "title": "Q4 MRO seasonal replenishment creates receipt mismatch",
        "type": "seasonality",
        "sources": ["Supplier profiles", "Purchase orders", "Goods receipt timing"],
        "correlation_strength": 0.82,
        "confidence": 0.80,
        "impact_estimate": "2-week buffer avoids expedited MRO invoices",
        "supplier_ids": ["SUP-MRO-004", "SUP-MRO-009"],
        "pattern": "q4_replenishment_receipt_lag",
        "first_detected": "2026-05-03T08:30:00Z",
        "detection_count": 11,
        "recommendation": "Add Q4 receipt buffer and pre-book MRO capacity.",
        "propagation_path": ["seasonal_demand", "goods_receipt_delay", "quantity_mismatch", "manual_review"],
    },
    {
        "discovery_id": "DISC-EXT-004",
        "title": "Payment term changes precede supplier exception bursts",
        "type": "payment_behavior",
        "sources": ["Payment terms", "Supplier profiles", "S2P invoice exceptions"],
        "correlation_strength": 0.79,
        "confidence": 0.77,
        "impact_estimate": "$130K discount leakage",
        "supplier_ids": ["SUP-PAY-003", "SUP-PAY-011"],
        "pattern": "payment_term_shift_exception_burst",
        "first_detected": "2026-05-03T13:45:00Z",
        "detection_count": 9,
        "recommendation": "Review payment term deltas before auto-approval threshold expansion.",
        "propagation_path": ["payment_terms", "supplier_exception_history", "auto_approve_hold", "discount_leakage"],
    },
    {
        "discovery_id": "DISC-EXT-005",
        "title": "Tax disclosure drift clusters around cross-border suppliers",
        "type": "regulatory_change",
        "sources": ["Tax rules", "ERP vendor master", "Invoice OCR"],
        "correlation_strength": 0.77,
        "confidence": 0.75,
        "impact_estimate": "$95K compliance exposure",
        "supplier_ids": ["SUP-TAX-004", "SUP-YANGTZE"],
        "pattern": "cross_border_tax_disclosure_drift",
        "first_detected": "2026-05-04T09:20:00Z",
        "detection_count": 8,
        "recommendation": "Publish updated tax template and verify supplier adoption.",
        "propagation_path": ["tax_rule_change", "format_compliance", "specialist_referral", "template_update"],
    },
    {
        "discovery_id": "DISC-EXT-006",
        "title": "Single-source dependency amplifies contract gap exceptions",
        "type": "supplier_concentration",
        "sources": ["Contract repository", "Supplier profiles", "Purchase orders"],
        "correlation_strength": 0.74,
        "confidence": 0.72,
        "impact_estimate": "$340K single-source exposure",
        "supplier_ids": ["SUP-CRIT-007"],
        "pattern": "single_source_contract_gap_amplification",
        "first_detected": "2026-05-04T15:05:00Z",
        "detection_count": 7,
        "recommendation": "Create emergency source path and split future awards.",
        "propagation_path": ["supplier_concentration", "contract_gap", "buyer_escalation", "emergency_source"],
    },
    {
        "discovery_id": "DISC-EXT-007",
        "title": "Duplicate invoice clusters follow supplier system migration",
        "type": "duplicate_risk",
        "sources": ["Supplier migration logs", "ERP vendor master", "Invoice exceptions"],
        "correlation_strength": 0.71,
        "confidence": 0.70,
        "impact_estimate": "$75K duplicate-payment exposure",
        "supplier_ids": ["SUP-CHI-004", "SUP-PAY-011"],
        "pattern": "system_migration_duplicate_cluster",
        "first_detected": "2026-05-05T11:10:00Z",
        "detection_count": 6,
        "recommendation": "Temporarily route migrated supplier invoices through duplicate-risk review.",
        "propagation_path": ["supplier_migration", "duplicate_score", "hold_for_review", "payment_block"],
    },
    {
        "discovery_id": "DISC-EXT-008",
        "title": "Commodity-linked contracts need pass-through threshold refresh",
        "type": "contract_gap",
        "sources": ["Commodity index", "Contract repository", "S2P invoice exceptions"],
        "correlation_strength": 0.68,
        "confidence": 0.69,
        "impact_estimate": "$160K contract leakage risk",
        "supplier_ids": ["SUP-ALT-SEA", "SUP-MRO-009"],
        "pattern": "commodity_pass_through_threshold_drift",
        "first_detected": "2026-05-05T16:40:00Z",
        "detection_count": 5,
        "recommendation": "Refresh pass-through thresholds before expansion of auto-approval.",
        "propagation_path": ["commodity_index", "contract_threshold", "price_variance", "flag_leakage"],
    },
)


@router.get("/alerts", response_model=GenericResponse)
@cached_static("discovery-alerts", copilot="s2p")
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


@router.get("/disruptions", response_model=GenericResponse)
@cached_static("discovery-disruptions", copilot="s2p")
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


def _extended_sorted() -> list[dict[str, Any]]:
    return sorted(
        (dict(discovery) for discovery in EXTENDED_DISCOVERIES),
        key=lambda discovery: float(discovery["correlation_strength"]),
        reverse=True,
    )


def _per_supplier(discoveries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    supplier_map: dict[str, dict[str, Any]] = {}
    for discovery in discoveries:
        for supplier_id in discovery["supplier_ids"]:
            row = supplier_map.setdefault(
                supplier_id,
                {
                    "supplier_id": supplier_id,
                    "discovery_count": 0,
                    "detection_count": 0,
                    "highest_correlation": 0.0,
                },
            )
            row["discovery_count"] += 1
            row["detection_count"] += int(discovery["detection_count"])
            row["highest_correlation"] = max(
                float(row["highest_correlation"]),
                float(discovery["correlation_strength"]),
            )
    return dict(sorted(supplier_map.items()))


def _by_type(discoveries: list[dict[str, Any]]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for discovery in discoveries:
        discovery_type = str(discovery["type"])
        distribution[discovery_type] = distribution.get(discovery_type, 0) + 1
    return dict(sorted(distribution.items()))


@router.get("/extended", response_model=GenericResponse)
@cached_static("discovery-extended", copilot="s2p")
def extended_discoveries() -> dict[str, Any]:
    """Return deterministic extended discovery graph examples for S2P."""

    discoveries = _extended_sorted()
    sources = {source for discovery in discoveries for source in discovery["sources"]}
    return {
        "discoveries": discoveries,
        "total": len(discoveries),
        "per_supplier": _per_supplier(discoveries),
        "by_type": _by_type(discoveries),
        "sources_connected": len(sources),
    }


@router.get("/supplier/{supplier_id}", response_model=GenericResponse)
def supplier_discoveries(supplier_id: str) -> dict[str, Any]:
    """Return extended discoveries involving one supplier."""

    discoveries = [
        discovery
        for discovery in _extended_sorted()
        if supplier_id in discovery["supplier_ids"]
    ]
    return {
        "supplier_id": supplier_id,
        "discoveries": discoveries,
        "total": len(discoveries),
        "total_detection_count": sum(int(discovery["detection_count"]) for discovery in discoveries),
    }


@router.get("/propagation/{discovery_id}", response_model=GenericResponse)
def discovery_propagation(discovery_id: str) -> dict[str, Any]:
    """Return propagation path metadata for one extended discovery."""

    discovery = next(
        (discovery for discovery in EXTENDED_DISCOVERIES if discovery["discovery_id"] == discovery_id),
        None,
    )
    if discovery is None:
        raise HTTPException(status_code=404, detail=f"Unknown discovery: {discovery_id}")
    return {
        "discovery_id": discovery["discovery_id"],
        "title": discovery["title"],
        "type": discovery["type"],
        "supplier_ids": list(discovery["supplier_ids"]),
        "propagation_path": list(discovery["propagation_path"]),
        "detection_count": discovery["detection_count"],
        "confidence": discovery["confidence"],
        "recommendation": discovery["recommendation"],
    }
