import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.routers import s2p_pvg

client = TestClient(app)


def test_impact_annual_target_is_680000():
    data = client.get("/api/s2p/pvg/impact", params={"period": "annual"}).json()

    assert data["annual_target"] == 680000
    assert data["total_savings"] == 680000


def test_impact_period_scaling():
    monthly = client.get("/api/s2p/pvg/impact", params={"period": "monthly"}).json()
    quarterly = client.get("/api/s2p/pvg/impact", params={"period": "quarterly"}).json()
    annual = client.get("/api/s2p/pvg/impact", params={"period": "annual"}).json()

    assert monthly["total_savings"] == round(annual["total_savings"] / 12, 2)
    assert quarterly["total_savings"] == round(annual["total_savings"] / 4, 2)


def test_impact_breakdown_has_three_categories_and_sums_to_total():
    data = client.get("/api/s2p/pvg/impact", params={"period": "annual"}).json()
    breakdown = data["breakdown"]
    total = sum(item["amount"] for item in breakdown.values())

    assert set(breakdown) == {"leakage_prevented", "cycle_time_saved", "auto_approve_efficiency"}
    assert sum(item["pct"] for item in breakdown.values()) == 100
    assert total == pytest.approx(data["total_savings"], abs=0.02)


def test_impact_invalid_period_422():
    response = client.get("/api/s2p/pvg/impact", params={"period": "weekly"})

    assert response.status_code == 422


def test_variants_returns_process_or_fixture_variants():
    response = client.get("/api/s2p/pvg/variants")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == len(data["variants"])
    assert data["variants"]
    assert {"variant_id", "variant_name", "volume", "median_cycle_minutes"}.issubset(data["variants"][0])


def test_variants_uses_true_median_for_even_activity_counts(monkeypatch):
    monkeypatch.setattr(
        s2p_pvg,
        "_load_process_data",
        lambda: {
            "variant": "Even Count",
            "total_cases": 4,
            "source": "test_fixture",
            "activities": [
                {"name": "A", "duration_minutes": 60},
                {"name": "B", "duration_minutes": 120},
                {"name": "C", "duration_minutes": 600},
                {"name": "D", "duration_minutes": 1200},
            ],
        },
    )

    data = client.get("/api/s2p/pvg/variants").json()

    assert data["variants"][0]["median_cycle_minutes"] == 360.0


def test_leakage_only_includes_invoices_satisfying_both_conditions():
    data = client.get("/api/s2p/pvg/leakage").json()

    assert data["count"] == len(data["flagged_invoices"])
    for invoice in data["flagged_invoices"]:
        assert invoice["amount_variance_ratio"] > 0.15
        assert invoice["commodity_index_correlation"] < 0.5


def test_leakage_sorted_descending_by_at_risk_amount():
    flagged = client.get("/api/s2p/pvg/leakage").json()["flagged_invoices"]
    values = [invoice["at_risk_amount"] for invoice in flagged]

    assert values == sorted(values, reverse=True)


def test_total_at_risk_equals_sum_of_flagged_amounts():
    data = client.get("/api/s2p/pvg/leakage").json()
    expected = round(sum(invoice["at_risk_amount"] for invoice in data["flagged_invoices"]), 2)

    assert data["total_at_risk"] == pytest.approx(expected, abs=0.01)


def test_cycle_time_returns_unavailable_or_valid_activities():
    response = client.get("/api/s2p/pvg/cycle-time")

    assert response.status_code == 200
    data = response.json()
    assert "available" in data
    if data["available"]:
        assert data["activities"]
        assert all({"name", "duration_minutes", "is_bottleneck"}.issubset(activity) for activity in data["activities"])
        assert data["total_median_minutes"] >= 0
    else:
        assert data["activities"] == []
        assert data["reason"] == "Celonis data not configured"


def test_all_pvg_endpoints_mounted():
    for path in (
        "/api/s2p/pvg/variants",
        "/api/s2p/pvg/impact",
        "/api/s2p/pvg/leakage",
        "/api/s2p/pvg/cycle-time",
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()


def test_no_soc_imports_or_vocabulary_in_pvg_router():
    text = Path("app/routers/s2p_pvg.py").read_text(encoding="utf-8").lower()

    for forbidden in (
        "from app.domains.soc",
        "import soc",
        "credential_access",
        "lateral_movement",
        "data_exfiltration",
        "escalate_soc",
        "suppress",
    ):
        assert forbidden not in text
