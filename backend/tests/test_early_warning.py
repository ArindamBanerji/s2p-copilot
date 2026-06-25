import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.routers.s2p_early_warning import compute_combined_severity, match_pattern
from app.services.supplier_profile_accumulator import accumulator


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_supplier_accumulator():
    accumulator.reset()
    yield
    accumulator.reset()


def test_early_warnings_returns_200():
    response = client.get("/api/s2p/suppliers/early-warnings")

    assert response.status_code == 200


def test_early_warnings_response_shape():
    data = client.get("/api/s2p/suppliers/early-warnings").json()

    assert {"warnings", "monitored_suppliers", "active_warnings", "patterns_detected"} <= set(data)
    assert data["monitored_suppliers"] == 10
    assert data["active_warnings"] == len(data["warnings"])


def test_yangtze_has_high_risk_warning():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]
    yangtze = next(warning for warning in warnings if warning["supplier_name"] == "Yangtze Raw Materials")

    assert yangtze["pattern"] == "financial_stress_delivery_failure"
    assert yangtze["risk_score"] == 0.78
    assert yangtze["confidence"] == 0.78
    assert "Qualify backup" in yangtze["recommendation"]
    assert yangtze["lead_time_weeks"] == 6
    assert {"OTIF", "exception_rate", "financial_health"} <= {
        signal["signal_name"] for signal in yangtze["signals"]
    }


def test_rhine_stahl_has_medium_warning():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]
    rhine = next(warning for warning in warnings if warning["supplier_name"] == "Rhine-Stahl Metals")

    assert rhine["pattern"] == "otif_erosion"
    assert rhine["risk_score"] == 0.45
    assert "Monitor" in rhine["recommendation"]
    assert rhine["lead_time_weeks"] == 12


def test_stable_suppliers_have_no_warnings():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]
    names = {warning["supplier_name"] for warning in warnings}

    assert "Novatek IT Services" not in names
    assert "Meridian Office Services" not in names


def test_risk_score_between_0_and_1():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]

    assert all(0.0 <= warning["risk_score"] <= 1.0 for warning in warnings)


def test_confidence_between_0_and_1():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]

    assert all(0.0 <= warning["confidence"] <= 1.0 for warning in warnings)


def test_signals_have_direction():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]

    assert warnings
    for warning in warnings:
        assert all(signal["direction"] in {"declining", "stable", "improving"} for signal in warning["signals"])


def test_pattern_field_populated():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]

    assert all(warning["pattern"] for warning in warnings)


def test_recommendation_populated():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]

    assert all(warning["recommendation"] for warning in warnings)


def test_lead_time_positive():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]

    assert all(warning["lead_time_weeks"] > 0 for warning in warnings)


def test_trend_signals_for_known_supplier():
    response = client.get("/api/s2p/suppliers/trend-signals", params={"supplier_id": "SUP-005"})

    assert response.status_code == 200
    data = response.json()
    assert data["supplier_id"] == "SUP-005"
    assert {signal["signal_name"] for signal in data["signals"]} >= {"OTIF", "exception_rate"}


def test_trend_signals_unknown_supplier():
    response = client.get("/api/s2p/suppliers/trend-signals", params={"supplier_id": "UNKNOWN"})

    assert response.status_code == 404


def test_warnings_sorted_by_risk_descending():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]
    scores = [warning["risk_score"] for warning in warnings]

    assert scores == sorted(scores, reverse=True)


def test_static_early_warning_routes_not_shadowed_by_supplier_id_route():
    early = client.get("/api/s2p/suppliers/early-warnings")
    trend = client.get("/api/s2p/suppliers/trend-signals", params={"supplier_id": "SUP-005"})

    assert early.status_code == 200
    assert "warnings" in early.json()
    assert trend.status_code == 200
    assert "signals" in trend.json()


def test_pattern_match_financial_stress():
    trends = [
        {"signal_name": "OTIF", "delta_pct": -10.0, "direction": "declining"},
        {"signal_name": "exception_rate", "delta_pct": 10.0, "direction": "declining"},
        {"signal_name": "pricing", "delta_pct": 10.0, "direction": "increasing"},
    ]

    match = match_pattern(trends)

    assert match is not None
    assert match["pattern"] == "financial_stress"


def test_pattern_match_operational():
    trends = [
        {"signal_name": "OTIF", "delta_pct": -10.0, "direction": "declining"},
        {"signal_name": "exception_rate", "delta_pct": 10.0, "direction": "declining"},
    ]

    match = match_pattern(trends)

    assert match is not None
    assert match["pattern"] == "operational_degradation"


def test_no_pattern_match():
    trends = [{"signal_name": "OTIF", "delta_pct": -5.0, "direction": "declining"}]

    assert match_pattern(trends) is None


def test_combined_severity_computation():
    trends = [
        {"signal_name": "OTIF", "delta_pct": -10.0, "direction": "declining"},
        {"signal_name": "exception_rate", "delta_pct": 10.0, "direction": "declining"},
        {"signal_name": "pricing", "delta_pct": 10.0, "direction": "increasing"},
    ]

    assert compute_combined_severity(trends) == 0.3


def test_days_to_impact_from_pattern():
    trends = [
        {"signal_name": "OTIF", "delta_pct": -10.0, "direction": "declining"},
        {"signal_name": "exception_rate", "delta_pct": 10.0, "direction": "declining"},
        {"signal_name": "pricing", "delta_pct": 10.0, "direction": "increasing"},
    ]

    assert match_pattern(trends)["days_to_impact"] == 45


def test_narrative_field_present():
    warnings = client.get("/api/s2p/suppliers/early-warnings").json()["warnings"]

    assert warnings
    assert all(isinstance(warning["narrative"], str) and warning["narrative"] for warning in warnings)


def test_confidence_from_signal_strength():
    weak = [
        {"signal_name": "OTIF", "delta_pct": -2.0, "direction": "declining"},
        {"signal_name": "exception_rate", "delta_pct": 2.0, "direction": "declining"},
    ]
    strong = [
        {"signal_name": "OTIF", "delta_pct": -20.0, "direction": "declining"},
        {"signal_name": "exception_rate", "delta_pct": 20.0, "direction": "declining"},
    ]

    assert match_pattern(strong)["confidence"] > match_pattern(weak)["confidence"]


def test_patterns_endpoint():
    response = client.get("/api/s2p/suppliers/early-warnings/patterns")

    assert response.status_code == 200
    data = response.json()
    assert {pattern["name"] for pattern in data["patterns"]} >= {"financial_stress", "operational_degradation"}


def test_pattern_match_market_pressure():
    trends = [{"signal_name": "pricing", "delta_pct": 12.0, "direction": "increasing"}]

    match = match_pattern(trends)

    assert match is not None
    assert match["pattern"] == "market_pressure"
