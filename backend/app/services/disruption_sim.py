"""Advisory S2P disruption simulation service."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import asdict
from typing import Any

DISRUPTION_TYPES = {
    "delay": {"cost_factor_per_week": 0.05, "desc": "Delivery delay"},
    "shutdown": {"cost_factor": 0.15, "desc": "Complete supplier shutdown"},
    "quality_drop": {"rework_factor": 0.10, "desc": "Quality degradation"},
    "price_spike": {"pass_through": 1.0, "desc": "Price increase"},
}


@dataclass
class SupplierProfile:
    supplier_id: str
    name: str
    categories: tuple[str, ...]
    otif: float
    lead_time_days: int
    cost_delta_per_unit: float = 0.0
    annual_spend: float = 1_000_000.0


class DisruptionSimulator:
    """What-if scenario modeling using learned supplier parameters.

    Advisory only -- no auto-action. Uses supplier profiles and lead-time
    signals to estimate impact and alternatives.
    """

    def __init__(
        self,
        suppliers: list[dict[str, Any]] | None = None,
        profiles: list[Any] | None = None,
    ) -> None:
        if profiles:
            self.suppliers = [self._profile_from_r18(profile) for profile in profiles]
            self._provenance = "live"
        elif suppliers:
            self.suppliers = [self._profile(row) for row in suppliers]
            self._provenance = "demo"
        else:
            self.suppliers = [self._profile(row) for row in _demo_suppliers()]
            self._provenance = "demo"

    def simulate(self, scenario: dict[str, Any]) -> dict[str, Any]:
        supplier = self._supplier(str(scenario.get("supplier_id") or "SUP-X"))
        categories = list(scenario.get("categories") or supplier.categories)
        magnitude_days = int(scenario.get("magnitude_days") or 14)
        disruption_type = str(scenario.get("type") or "delay")
        affected_po_count = int(scenario.get("affected_po_count") or max(1, len(categories) * 4))
        spend = float(scenario.get("spend") or supplier.annual_spend)
        estimated_cost = self._estimate_cost(disruption_type, magnitude_days, spend, scenario)
        alternatives = self._find_alternatives(supplier.supplier_id, categories)
        severity = self._classify_severity(estimated_cost, len(categories), alternatives)
        recommendation = self._recommendation(alternatives, severity)
        result = {
            "scenario": {
                "supplier_id": supplier.supplier_id,
                "supplier": supplier.name,
                "type": disruption_type,
                "magnitude_days": magnitude_days,
            },
            "impact": {
                "affected_categories": categories,
                "affected_po_count": affected_po_count,
                "estimated_cost": round(estimated_cost, 2),
                "severity": severity,
            },
            "alternatives": alternatives,
            "recommendation": recommendation,
            "provenance": self._provenance,
        }
        result["narrative"] = self._narrative(result)
        return result

    def batch_simulate(self, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [self.simulate(scenario) for scenario in scenarios]

    def _find_alternatives(self, supplier_id: str, categories: list[str]) -> list[dict[str, Any]]:
        source = self._supplier(supplier_id)
        alternatives = []
        category_set = set(categories)
        for candidate in self.suppliers:
            if candidate.supplier_id == supplier_id:
                continue
            if not category_set.intersection(candidate.categories):
                continue
            alternatives.append(
                {
                    "supplier_id": candidate.supplier_id,
                    "name": candidate.name,
                    "otif": round(candidate.otif, 2),
                    "lead_time_delta_days": candidate.lead_time_days - source.lead_time_days,
                    "cost_delta_per_unit": round(candidate.cost_delta_per_unit, 2),
                }
            )
        alternatives.sort(key=lambda row: (-float(row["otif"]), float(row["lead_time_delta_days"])))
        return alternatives

    def _classify_severity(self, cost: float, category_count: int, alternatives: list[dict[str, Any]] | None = None) -> str:
        if not alternatives:
            return "critical"
        if cost > 100_000 or category_count >= 3:
            return "critical"
        if cost > 50_000 or category_count >= 2:
            return "major"
        return "minor"

    def _estimate_cost(self, disruption_type: str, days: int, spend: float, scenario: dict[str, Any]) -> float:
        if disruption_type == "delay":
            return spend * DISRUPTION_TYPES["delay"]["cost_factor_per_week"] * (days / 7.0)
        if disruption_type == "shutdown":
            return spend * DISRUPTION_TYPES["shutdown"]["cost_factor"]
        if disruption_type == "quality_drop":
            return spend * DISRUPTION_TYPES["quality_drop"]["rework_factor"]
        if disruption_type == "price_spike":
            return spend * float(scenario.get("price_increase_pct") or 0.0) * DISRUPTION_TYPES["price_spike"]["pass_through"]
        return spend * 0.05

    def _supplier(self, supplier_id: str) -> SupplierProfile:
        for supplier in self.suppliers:
            if supplier.supplier_id == supplier_id:
                return supplier
        return SupplierProfile(
            supplier_id=supplier_id,
            name=str(supplier_id),
            categories=("raw_materials", "components", "packaging"),
            otif=0.82,
            lead_time_days=21,
            annual_spend=500_000.0,
        )

    def _recommendation(self, alternatives: list[dict[str, Any]], severity: str) -> str:
        if not alternatives:
            return "No qualified alternative found. Escalate sourcing review and prepare executive risk acceptance."
        best = alternatives[0]
        return f"Qualify {best['name']} as backup. OTIF {best['otif']:.0%}, lead time delta {best['lead_time_delta_days']:+d} days."

    def _narrative(self, result: dict[str, Any]) -> str:
        scenario = result["scenario"]
        impact = result["impact"]
        alternatives = result["alternatives"]
        alt_text = self._alternatives_text(alternatives)
        duration = self._duration_text(int(scenario["magnitude_days"]))
        return (
            f"What if {scenario['supplier']} delays {duration}? "
            f"Impact: {len(impact['affected_categories'])} categories, {impact['affected_po_count']} POs, "
            f"estimated ${impact['estimated_cost']:,.0f}. {alt_text} Severity: {impact['severity']}."
        )

    def _alternatives_text(self, alternatives: list[dict[str, Any]]) -> str:
        if not alternatives:
            return "No qualified alternative supplier found."
        rows = []
        for alternative in alternatives[:2]:
            cost_delta = float(alternative.get("cost_delta_per_unit") or 0.0)
            cost_text = "" if cost_delta == 0.0 else f", +${cost_delta:,.0f} per unit"
            rows.append(
                f"{alternative['name']} (OTIF {alternative['otif']:.0%}, "
                f"{alternative['lead_time_delta_days']:+d} days{cost_text})"
            )
        return "Alternatives: " + "; ".join(rows) + "."

    def _duration_text(self, days: int) -> str:
        if days % 7 == 0:
            weeks = days // 7
            return f"{weeks} week" if weeks == 1 else f"{weeks} weeks"
        return f"{days} days"

    def _profile(self, row: dict[str, Any]) -> SupplierProfile:
        return SupplierProfile(
            supplier_id=str(row.get("supplier_id")),
            name=str(row.get("name") or row.get("supplier_name") or row.get("supplier_id")),
            categories=tuple(row.get("categories") or [row.get("category") or "raw_materials"]),
            otif=float(row.get("otif") or row.get("otif_score") or 0.85),
            lead_time_days=int(float(row.get("lead_time_days") or row.get("avg_lead_time_days") or 21)),
            cost_delta_per_unit=float(row.get("cost_delta_per_unit") or 0.0),
            annual_spend=float(row.get("annual_spend") or row.get("spend") or 1_000_000.0),
        )

    def _profile_from_r18(self, profile: Any) -> SupplierProfile:
        row = asdict(profile) if hasattr(profile, "__dataclass_fields__") else dict(profile)
        categories = tuple(row.get("categories") or [row.get("category") or "raw_materials"])
        otif = row.get("otif")
        if otif is None and row.get("otif_by_quarter"):
            values = list(row["otif_by_quarter"].values())
            otif = sum(float(value) for value in values) / max(len(values), 1)
        lead_time = row.get("avg_lead_time_days") or row.get("lead_time_days") or 21
        spend = row.get("annual_spend") or row.get("avg_invoice_amount") or 1_000_000
        return SupplierProfile(
            supplier_id=str(row.get("supplier_id")),
            name=str(row.get("supplier_name") or row.get("name") or row.get("supplier_id")),
            categories=categories,
            otif=float(otif or 0.85),
            lead_time_days=int(float(lead_time)),
            cost_delta_per_unit=float(row.get("cost_delta_per_unit") or 0.0),
            annual_spend=float(spend),
        )


def _demo_suppliers() -> list[dict[str, Any]]:
    return [
        {
            "supplier_id": "SUP-X",
            "name": "Supplier X",
            "categories": ["raw_materials", "components", "packaging"],
            "otif": 0.82,
            "lead_time_days": 21,
            "annual_spend": 1_000_000.0,
        },
        {
            "supplier_id": "SUP-Y",
            "name": "Supplier Y",
            "categories": ["raw_materials", "components", "packaging"],
            "otif": 0.91,
            "lead_time_days": 24,
            "cost_delta_per_unit": 0.0,
        },
        {
            "supplier_id": "SUP-Z",
            "name": "Supplier Z",
            "categories": ["raw_materials", "components", "packaging"],
            "otif": 0.87,
            "lead_time_days": 26,
            "cost_delta_per_unit": 2.0,
        },
    ]
