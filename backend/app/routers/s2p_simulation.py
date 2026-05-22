"""Deterministic S2P disruption simulation endpoints."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/s2p/simulation", tags=["s2p-simulation"])


SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "scenario_id": "SIM-001",
        "name": "Tariff Shock - Southeast Asia",
        "type": "tariff_increase",
        "description": "Import tariff increase hits high-volume electronics and packaging suppliers.",
        "affected_suppliers": ["SUP-SEA-001", "SUP-SEA-014", "SUP-SEA-021"],
        "affected_categories": ["price_variance", "contract_gap"],
        "trigger": "New tariff schedule effective next quarter",
        "impact": {
            "conservation_impact": "RED",
            "estimated_quarterly_cost": 420000.0,
            "recovery_time_days": 45,
            "auto_approve_rate_change": -0.18,
            "exception_rate_increase_pct": 0.32,
            "supplier_count": 3,
            "contract_renegotiation_required": True,
        },
        "mitigation": {
            "recommended": "dual_source_tariff_exposed_items",
            "available_actions": [
                {
                    "action": "dual_source_tariff_exposed_items",
                    "effort": "medium",
                    "impact_reduction": 0.35,
                    "description": "Shift exposed line items to already-qualified backup suppliers.",
                },
                {
                    "action": "pre_buy_inventory",
                    "effort": "low",
                    "impact_reduction": 0.20,
                    "description": "Pre-buy critical SKUs before tariff effective date.",
                },
            ],
        },
    },
    {
        "scenario_id": "SIM-002",
        "name": "Supplier Failure - Critical Single Source",
        "type": "supplier_failure",
        "description": "A single-source supplier misses fulfillment on contract-bound invoices.",
        "affected_suppliers": ["SUP-CRIT-007"],
        "affected_categories": ["quantity_mismatch", "contract_gap"],
        "trigger": "Supplier capacity disruption and delayed goods receipts",
        "impact": {
            "conservation_impact": "RED",
            "estimated_quarterly_cost": 610000.0,
            "recovery_time_days": 60,
            "auto_approve_rate_change": -0.22,
            "exception_rate_increase_pct": 0.41,
            "single_source_dependency": True,
            "open_invoice_count": 38,
        },
        "mitigation": {
            "recommended": "activate_emergency_source",
            "available_actions": [
                {
                    "action": "activate_emergency_source",
                    "effort": "high",
                    "impact_reduction": 0.40,
                    "description": "Use emergency source and route exceptions to buyer escalation.",
                },
                {
                    "action": "split_award_contract",
                    "effort": "medium",
                    "impact_reduction": 0.28,
                    "description": "Split future awards across approved alternates.",
                },
            ],
        },
    },
    {
        "scenario_id": "SIM-003",
        "name": "Demand Spike - Seasonal Peak",
        "type": "demand_spike",
        "description": "Seasonal demand spike increases quantity and price variance exceptions.",
        "affected_suppliers": ["SUP-SEAS-003", "SUP-SEAS-009"],
        "affected_categories": ["quantity_mismatch", "price_variance"],
        "trigger": "Forecasted demand exceeds baseline by 28 percent",
        "impact": {
            "conservation_impact": "AMBER",
            "estimated_quarterly_cost": 180000.0,
            "recovery_time_days": 21,
            "auto_approve_rate_change": -0.08,
            "exception_rate_increase_pct": 0.18,
            "forecast_delta_pct": 0.28,
            "expedite_risk": True,
        },
        "mitigation": {
            "recommended": "increase_receipt_matching_tolerance",
            "available_actions": [
                {
                    "action": "increase_receipt_matching_tolerance",
                    "effort": "low",
                    "impact_reduction": 0.22,
                    "description": "Temporarily widen receipt matching tolerance for seasonal SKUs.",
                },
                {
                    "action": "pre_book_capacity",
                    "effort": "medium",
                    "impact_reduction": 0.30,
                    "description": "Pre-book seasonal capacity with preferred distributors.",
                },
            ],
        },
    },
    {
        "scenario_id": "SIM-004",
        "name": "Regulatory Change - Tax Code Update",
        "type": "regulatory",
        "description": "Tax compliance rule change increases format and regulatory validation failures.",
        "affected_suppliers": ["SUP-TAX-004", "SUP-TAX-016", "SUP-TAX-018"],
        "affected_categories": ["format_compliance", "contract_gap"],
        "trigger": "New regional tax code and invoice disclosure requirements",
        "impact": {
            "conservation_impact": "GREEN",
            "estimated_quarterly_cost": 95000.0,
            "recovery_time_days": 14,
            "auto_approve_rate_change": -0.04,
            "exception_rate_increase_pct": 0.11,
            "tax_rule_count": 6,
            "template_update_required": True,
        },
        "mitigation": {
            "recommended": "publish_tax_template_update",
            "available_actions": [
                {
                    "action": "publish_tax_template_update",
                    "effort": "medium",
                    "impact_reduction": 0.45,
                    "description": "Publish revised tax templates and run supplier enablement.",
                },
                {
                    "action": "temporary_manual_review",
                    "effort": "low",
                    "impact_reduction": 0.16,
                    "description": "Temporarily review affected tax invoices manually.",
                },
            ],
        },
    },
)


def _scenario_summary(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario_id": scenario["scenario_id"],
        "name": scenario["name"],
        "type": scenario["type"],
        "description": scenario["description"],
        "affected_suppliers": list(scenario["affected_suppliers"]),
        "affected_categories": list(scenario["affected_categories"]),
        "trigger": scenario["trigger"],
        "conservation_impact": scenario["impact"]["conservation_impact"],
        "estimated_quarterly_cost": scenario["impact"]["estimated_quarterly_cost"],
        "recovery_time_days": scenario["impact"]["recovery_time_days"],
    }


def _find_scenario(scenario_id: str) -> dict[str, Any] | None:
    return next((scenario for scenario in SCENARIOS if scenario["scenario_id"] == scenario_id), None)


def _apply_mitigation(impact: dict[str, Any], impact_reduction: float) -> dict[str, Any]:
    mitigated: dict[str, Any] = {}
    for key, value in impact.items():
        if isinstance(value, bool):
            mitigated[key] = value
        elif isinstance(value, (int, float)):
            mitigated[key] = round(float(value) * (1.0 - impact_reduction), 6)
        else:
            mitigated[key] = value
    return mitigated


def _find_mitigation(actions: list[dict[str, Any]], mitigation: str | None) -> dict[str, Any] | None:
    if not mitigation:
        return None
    return next((action for action in actions if action["action"] == mitigation), None)


@router.get("/scenarios")
def list_scenarios() -> dict[str, Any]:
    scenarios = [_scenario_summary(scenario) for scenario in SCENARIOS]
    return {"scenarios": scenarios, "total": len(scenarios)}


@router.get("/scenarios/{scenario_id}")
def scenario_detail(scenario_id: str) -> dict[str, Any]:
    scenario = _find_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario_id}")
    return deepcopy(scenario)


@router.get("/what-if/{scenario_id}")
def scenario_what_if(scenario_id: str, mitigation: str | None = None) -> dict[str, Any]:
    scenario = _find_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario: {scenario_id}")

    base_impact = deepcopy(scenario["impact"])
    actions = scenario["mitigation"]["available_actions"]
    mitigation_detail = _find_mitigation(actions, mitigation)
    if mitigation_detail is None:
        return {
            "scenario_id": scenario_id,
            "mitigation_applied": None,
            "base_impact": base_impact,
            "mitigated_impact": base_impact,
            "available_mitigations": actions,
        }

    impact_reduction = float(mitigation_detail["impact_reduction"])
    return {
        "scenario_id": scenario_id,
        "mitigation_applied": mitigation_detail["action"],
        "impact_reduction": impact_reduction,
        "mitigation_detail": mitigation_detail,
        "base_impact": base_impact,
        "mitigated_impact": _apply_mitigation(base_impact, impact_reduction),
        "available_mitigations": actions,
    }


@router.get("/impact-summary")
def impact_summary() -> dict[str, Any]:
    impacts = [scenario["impact"] for scenario in SCENARIOS]
    return {
        "total_scenarios": len(SCENARIOS),
        "total_quarterly_exposure": sum(float(impact["estimated_quarterly_cost"]) for impact in impacts),
        "worst_case_recovery_days": max(int(impact["recovery_time_days"]) for impact in impacts),
        "scenarios_causing_red": sum(1 for impact in impacts if impact["conservation_impact"] == "RED"),
        "scenarios_causing_amber": sum(1 for impact in impacts if impact["conservation_impact"] == "AMBER"),
        "scenarios_green_safe": sum(1 for impact in impacts if impact["conservation_impact"] == "GREEN"),
    }
