import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.services.disruption_sim import DisruptionSimulator


def test_delay_impact():
    result = DisruptionSimulator().simulate(
        {
            "supplier_id": "SUP-X",
            "type": "delay",
            "magnitude_days": 14,
            "spend": 1_400_000,
            "affected_po_count": 12,
        }
    )

    assert result["impact"]["estimated_cost"] == 140_000.0
    assert result["impact"]["affected_po_count"] == 12
    assert len(result["impact"]["affected_categories"]) == 3


def test_shutdown_critical():
    result = DisruptionSimulator().simulate({"supplier_id": "SUP-X", "type": "shutdown"})

    assert result["impact"]["severity"] == "critical"


def test_quality_drop_rework():
    result = DisruptionSimulator().simulate({"supplier_id": "SUP-X", "type": "quality_drop", "spend": 200_000})

    assert result["impact"]["estimated_cost"] == 20_000.0


def test_price_spike_passthrough():
    result = DisruptionSimulator().simulate(
        {"supplier_id": "SUP-X", "type": "price_spike", "spend": 500_000, "price_increase_pct": 0.20}
    )

    assert result["impact"]["estimated_cost"] == 100_000.0


def test_alternatives_sorted_by_otif():
    result = DisruptionSimulator().simulate({"supplier_id": "SUP-X", "type": "delay"})

    otifs = [row["otif"] for row in result["alternatives"]]
    assert otifs == sorted(otifs, reverse=True)


def test_alternatives_include_deltas():
    result = DisruptionSimulator().simulate({"supplier_id": "SUP-X", "type": "delay"})

    assert {"lead_time_delta_days", "cost_delta_per_unit"} <= set(result["alternatives"][0])


def test_sole_source_critical():
    simulator = DisruptionSimulator(
        [
            {
                "supplier_id": "ONLY",
                "name": "Only Source",
                "categories": ["components"],
                "otif": 0.96,
                "lead_time_days": 14,
                "annual_spend": 50_000,
            }
        ]
    )
    result = simulator.simulate({"supplier_id": "ONLY", "type": "delay", "categories": ["components"]})

    assert result["alternatives"] == []
    assert result["impact"]["severity"] == "critical"


def test_severity_thresholds():
    simulator = DisruptionSimulator()

    assert simulator._classify_severity(120_000, 3, [{"name": "Backup"}]) == "critical"
    assert simulator._classify_severity(60_000, 1, [{"name": "Backup"}]) == "major"
    assert simulator._classify_severity(10_000, 1, [{"name": "Backup"}]) == "minor"


def test_batch_simulate():
    results = DisruptionSimulator().batch_simulate(
        [
            {"supplier_id": "SUP-X", "type": "delay"},
            {"supplier_id": "SUP-X", "type": "shutdown"},
            {"supplier_id": "SUP-X", "type": "price_spike", "price_increase_pct": 0.1},
        ]
    )

    assert len(results) == 3
    assert all("narrative" in result for result in results)


def test_narrative_present():
    result = DisruptionSimulator().simulate({"supplier_id": "SUP-X", "type": "delay", "magnitude_days": 14})

    assert "narrative" in result
    assert "What if Supplier X delays 2 weeks?" in result["narrative"]
    assert "Severity:" in result["narrative"]


def test_uses_profiles_when_provided():
    profiles = [
        {
            "supplier_id": "LIVE-X",
            "supplier_name": "Live Supplier X",
            "categories": ["components"],
            "otif": 0.77,
            "avg_lead_time_days": 18,
            "annual_spend": 250_000,
        },
        {
            "supplier_id": "LIVE-Y",
            "supplier_name": "Live Supplier Y",
            "categories": ["components"],
            "otif": 0.94,
            "avg_lead_time_days": 20,
        },
    ]
    result = DisruptionSimulator(profiles=profiles).simulate({"supplier_id": "LIVE-X", "categories": ["components"]})

    assert result["provenance"] == "live"
    assert result["scenario"]["supplier"] == "Live Supplier X"
    assert result["alternatives"][0]["name"] == "Live Supplier Y"


def test_demo_fallback_labeled():
    result = DisruptionSimulator().simulate({"supplier_id": "SUP-X", "type": "delay"})

    assert result["provenance"] == "demo"


def test_narrative_includes_both_alternatives():
    result = DisruptionSimulator().simulate({"supplier_id": "SUP-X", "type": "delay", "magnitude_days": 14})

    assert "Supplier Y" in result["narrative"]
    assert "Supplier Z" in result["narrative"]
    assert "+$2 per unit" in result["narrative"]
