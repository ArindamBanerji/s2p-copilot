import os
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app
from app.routers.s2p_payment import (
    compute_cash_flow_benefit,
    compute_dpo_portfolio,
    compute_early_pay_value,
    compute_payment_otif_correlation,
)
from app.services.supplier_profile_accumulator import accumulator


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_supplier_accumulator():
    accumulator.reset()
    yield
    accumulator.reset()


def _strategies() -> list[dict]:
    return client.get("/api/s2p/suppliers/payment-strategy").json()["strategies"]


def _by_name(marker: str) -> dict:
    return next(row for row in _strategies() if marker in row["supplier_name"])


def test_payment_strategy_returns_200():
    response = client.get("/api/s2p/suppliers/payment-strategy")

    assert response.status_code == 200


def test_payment_strategy_response_shape():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert {
        "strategies",
        "total_discount_opportunity",
        "suppliers_analyzed",
        "dpo_improvement_days",
        "summary",
    } <= set(data)
    assert {
        "supplier_id",
        "supplier_name",
        "current_terms",
        "recommended_strategy",
        "reason",
        "payment_otif_correlation",
        "discount_opportunity",
        "risk_if_delayed",
        "confidence",
    } <= set(data["strategies"][0])


def test_all_10_suppliers_have_strategy():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert data["suppliers_analyzed"] == 10
    assert len(data["strategies"]) == 10
    assert len({row["supplier_id"] for row in data["strategies"]}) == 10


def test_aster_is_early_pay():
    aster = _by_name("Aster")

    assert aster["recommended_strategy"] == "early_pay"
    assert aster["payment_otif_correlation"] == 0.72
    assert aster["discount_opportunity"] == 180_000.0
    assert "Early pay improves OTIF" in aster["reason"]


def test_yangtze_is_early_pay():
    yangtze = _by_name("Yangtze")

    assert yangtze["recommended_strategy"] == "early_pay"
    assert yangtze["payment_otif_correlation"] == 0.65
    assert yangtze["discount_opportunity"] == 120_000.0
    assert "exceeds 45 days" in yangtze["reason"]


def test_rhine_stahl_is_early_pay():
    rhine = _by_name("Rhine-Stahl")

    assert rhine["recommended_strategy"] == "early_pay"
    assert rhine["payment_otif_correlation"] == 0.58
    assert rhine["discount_opportunity"] == 40_000.0
    assert "Quality improves" in rhine["reason"]


def test_meridian_is_extend():
    meridian = _by_name("Meridian")

    assert meridian["recommended_strategy"] == "extend"
    assert meridian["payment_otif_correlation"] == -0.02
    assert "DPO +8 days" in meridian["reason"]


def test_northstar_is_on_time():
    northstar = _by_name("Northstar")

    assert northstar["recommended_strategy"] == "on_time"
    assert northstar["payment_otif_correlation"] == 0.15
    assert "Net-30" in northstar["reason"]


def test_total_discount_matches_sum():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert data["total_discount_opportunity"] == sum(
        row["discount_opportunity"] for row in data["strategies"]
    )


def test_total_discount_is_340k():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert data["total_discount_opportunity"] == 340_000.0


def test_dpo_improvement_positive():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert data["dpo_improvement_days"] > 0


def test_dpo_improvement_is_8_days():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert data["dpo_improvement_days"] == 8.0


def test_correlation_in_valid_range():
    assert all(-1.0 <= row["payment_otif_correlation"] <= 1.0 for row in _strategies())


def test_confidence_in_valid_range():
    assert all(0.0 <= row["confidence"] <= 1.0 for row in _strategies())


def test_strategies_limited_to_three_values():
    assert {row["recommended_strategy"] for row in _strategies()} <= {"early_pay", "on_time", "extend"}


def test_payment_behavior_single_supplier():
    response = client.get("/api/s2p/suppliers/payment-behavior", params={"supplier_id": "SUP-001"})

    assert response.status_code == 200
    data = response.json()
    assert data["supplier_id"] == "SUP-001"
    assert data["supplier_name"] == "Aster Industrial Chemicals"
    assert data["current_terms"] == "Net 45"
    assert data["recommended_strategy"] == "early_pay"


def test_payment_behavior_unknown_supplier():
    response = client.get("/api/s2p/suppliers/payment-behavior", params={"supplier_id": "UNKNOWN"})

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_summary_string_populated():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert data["summary"] == "3 early-pay ($340K/yr), 4 on-time, 3 extend (+8 DPO days)"


def test_payment_routes_not_shadowed_by_supplier_id_route():
    strategy_response = client.get("/api/s2p/suppliers/payment-strategy")
    behavior_response = client.get("/api/s2p/suppliers/payment-behavior", params={"supplier_id": "SUP-001"})

    assert strategy_response.status_code == 200
    assert behavior_response.status_code == 200
    assert "strategies" in strategy_response.json()
    assert behavior_response.json()["supplier_id"] == "SUP-001"


def test_strategy_sort_order():
    rows = _strategies()
    order = {"early_pay": 0, "on_time": 1, "extend": 2}
    sort_keys = [
        (
            order[row["recommended_strategy"]],
            -row["discount_opportunity"] if row["recommended_strategy"] == "early_pay" else 0.0,
            row["supplier_name"],
        )
        for row in rows
    ]

    assert sort_keys == sorted(sort_keys)
    assert [row["discount_opportunity"] for row in rows[:3]] == [180_000.0, 120_000.0, 40_000.0]


def test_otif_correlation_negative():
    history = [
        {"supplier_id": "SUP-W", "payment_days": 10, "otif": 0.96},
        {"supplier_id": "SUP-W", "payment_days": 20, "otif": 0.90},
        {"supplier_id": "SUP-W", "payment_days": 30, "otif": 0.84},
    ]

    assert compute_payment_otif_correlation("SUP-W", history) < 0


def test_otif_correlation_zero():
    history = [
        {"supplier_id": "SUP-Y", "payment_days": 10, "otif": 0.9},
        {"supplier_id": "SUP-Y", "payment_days": 20, "otif": 0.9},
    ]

    assert compute_payment_otif_correlation("SUP-Y", history) == 0.0


def test_otif_correlation_positive():
    history = [
        {"supplier_id": "SUP-P", "payment_days": 10, "otif": 0.84},
        {"supplier_id": "SUP-P", "payment_days": 20, "otif": 0.90},
        {"supplier_id": "SUP-P", "payment_days": 30, "otif": 0.96},
    ]

    assert compute_payment_otif_correlation("SUP-P", history) > 0


def test_otif_correlation_small_sample():
    history = [{"supplier_id": "SUP-S", "payment_days": 10, "otif": 0.96}]

    assert compute_payment_otif_correlation("SUP-S", history) == 0.0


def test_dpo_portfolio():
    strategies = [
        {"annual_spend": 1_000_000.0, "dpo_impact_days": 8.0, "cash_flow_benefit": 1000.0},
        {"annual_spend": 3_000_000.0, "dpo_impact_days": 4.0, "cash_flow_benefit": 2000.0},
    ]

    result = compute_dpo_portfolio(strategies)

    assert result["portfolio_dpo_improvement"] == 5.0
    assert result["cash_flow_benefit"] == 3000.0


def test_early_pay_annualized_return():
    result = compute_early_pay_value({"capture_rate": 1.0}, 17_000_000.0)

    assert result["discount_captured"] == 340_000.0
    assert result["annualized_return_pct"] == 36.7


def test_cash_flow_benefit():
    result = compute_cash_flow_benefit(10_000_000.0, 8.0)

    assert result == 10958.9


def test_portfolio_endpoint():
    response = client.get("/api/s2p/suppliers/payment-portfolio")

    assert response.status_code == 200
    data = response.json()
    assert "portfolio_dpo_improvement" in data
    assert "total_annual_benefit" in data


def test_narrative_field_present():
    strategies = _strategies()

    assert strategies
    assert all(isinstance(row["narrative"], str) and row["narrative"] for row in strategies)


def test_total_annual_benefit():
    data = client.get("/api/s2p/suppliers/payment-strategy").json()

    assert data["total_annual_benefit"] == data["total_discount_opportunity"] + data["cash_flow_benefit"]
