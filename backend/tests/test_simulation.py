import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app
from app.routers.s2p_simulation import SCENARIOS


client = TestClient(app)


def test_list_scenarios_200():
    response = client.get("/api/s2p/simulation/scenarios")

    assert response.status_code == 200


def test_scenario_has_required_fields():
    scenario = client.get("/api/s2p/simulation/scenarios").json()["scenarios"][0]

    assert {
        "scenario_id",
        "name",
        "type",
        "description",
        "affected_suppliers",
        "affected_categories",
        "trigger",
        "conservation_impact",
        "estimated_quarterly_cost",
        "recovery_time_days",
    } <= set(scenario)


def test_get_scenario_detail():
    response = client.get("/api/s2p/simulation/scenarios/SIM-001")

    assert response.status_code == 200
    data = response.json()
    assert data["scenario_id"] == "SIM-001"
    assert {"conservation_impact", "estimated_quarterly_cost", "recovery_time_days"} <= set(data["impact"])
    assert "available_actions" in data["mitigation"]


def test_get_scenario_not_found():
    response = client.get("/api/s2p/simulation/scenarios/UNKNOWN")

    assert response.status_code == 404


def test_all_scenario_types():
    data = client.get("/api/s2p/simulation/scenarios").json()

    assert {row["type"] for row in data["scenarios"]} == {
        "tariff_increase",
        "supplier_failure",
        "demand_spike",
        "regulatory",
    }


def test_conservation_impacts_varied():
    impacts = {scenario["impact"]["conservation_impact"] for scenario in SCENARIOS}

    assert {"RED", "AMBER", "GREEN"} <= impacts


def test_what_if_no_mitigation():
    response = client.get("/api/s2p/simulation/what-if/SIM-001")

    assert response.status_code == 200
    data = response.json()
    assert data["mitigation_applied"] is None
    assert data["base_impact"] == data["mitigated_impact"]
    assert "available_mitigations" in data


def test_available_mitigations_have_required_schema():
    data = client.get("/api/s2p/simulation/what-if/SIM-001").json()

    for mitigation in data["available_mitigations"]:
        assert {"action", "effort", "impact_reduction", "description"} <= set(mitigation)
        assert isinstance(mitigation["impact_reduction"], (int, float))
        assert 0.0 <= mitigation["impact_reduction"] <= 1.0


def test_what_if_with_mitigation():
    no_mitigation = client.get("/api/s2p/simulation/what-if/SIM-001").json()
    action = no_mitigation["available_mitigations"][0]["action"]

    response = client.get(
        "/api/s2p/simulation/what-if/SIM-001",
        params={"mitigation": action},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["mitigation_applied"] == action
    assert data["impact_reduction"] == no_mitigation["available_mitigations"][0]["impact_reduction"]
    assert data["mitigation_detail"] == no_mitigation["available_mitigations"][0]


def test_selected_mitigation_detail_has_required_schema():
    response = client.get(
        "/api/s2p/simulation/what-if/SIM-002",
        params={"mitigation": "activate_emergency_source"},
    )

    assert response.status_code == 200
    detail = response.json()["mitigation_detail"]
    assert {"action", "effort", "impact_reduction", "description"} <= set(detail)
    assert detail["action"] == "activate_emergency_source"
    assert 0.0 <= detail["impact_reduction"] <= 1.0


def test_mitigation_reduces_cost():
    data = client.get(
        "/api/s2p/simulation/what-if/SIM-002",
        params={"mitigation": "activate_emergency_source"},
    ).json()

    assert data["mitigated_impact"]["estimated_quarterly_cost"] < data["base_impact"]["estimated_quarterly_cost"]
    assert data["mitigated_impact"]["single_source_dependency"] is True


def test_what_if_not_found():
    response = client.get("/api/s2p/simulation/what-if/UNKNOWN")

    assert response.status_code == 404


def test_what_if_unknown_mitigation_returns_base():
    response = client.get("/api/s2p/simulation/what-if/SIM-003", params={"mitigation": "not_real"})

    assert response.status_code == 200
    data = response.json()
    assert data["mitigation_applied"] is None
    assert data["base_impact"] == data["mitigated_impact"]


def test_impact_summary_200():
    response = client.get("/api/s2p/simulation/impact-summary")

    assert response.status_code == 200


def test_impact_summary_costs_sum():
    response = client.get("/api/s2p/simulation/impact-summary")
    data = response.json()
    expected = sum(float(scenario["impact"]["estimated_quarterly_cost"]) for scenario in SCENARIOS)

    assert data["total_scenarios"] == 4
    assert data["total_quarterly_exposure"] == expected
    assert data["worst_case_recovery_days"] == 60


def test_scenarios_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/simulation/scenarios" in paths
    assert "/api/s2p/simulation/scenarios/{scenario_id}" in paths


def test_what_if_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/simulation/what-if/{scenario_id}" in paths


def test_impact_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/simulation/impact-summary" in paths
