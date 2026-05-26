import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def assert_json_safe(value):
    if isinstance(value, dict):
        for item in value.values():
            assert_json_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_json_safe(item)
    elif isinstance(value, float):
        assert math.isfinite(value)
    else:
        assert value is None or isinstance(value, (str, int, bool))


def assert_dict_response(response):
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)
    return data


def test_simulation_scenarios_returns_list():
    data = assert_dict_response(client.get("/api/s2p/simulation/scenarios"))

    assert isinstance(data["scenarios"], list)
    assert data["total"] == len(data["scenarios"])


def test_simulation_scenario_detail_returns_known_id():
    data = assert_dict_response(client.get("/api/s2p/simulation/scenarios/SIM-001"))

    assert data["scenario_id"] == "SIM-001"
    assert isinstance(data["impact"], dict)


def test_simulation_scenario_detail_unknown_id_returns_404():
    response = client.get("/api/s2p/simulation/scenarios/UNKNOWN-SCENARIO")

    assert response.status_code == 404


def test_simulation_what_if_applies_known_mitigation():
    data = assert_dict_response(
        client.get(
            "/api/s2p/simulation/what-if/SIM-001",
            params={"mitigation": "dual_source_tariff_exposed_items"},
        )
    )

    assert data["scenario_id"] == "SIM-001"
    assert data["mitigation_applied"] == "dual_source_tariff_exposed_items"


def test_simulation_what_if_unknown_mitigation_returns_base_impact():
    data = assert_dict_response(
        client.get("/api/s2p/simulation/what-if/SIM-001", params={"mitigation": "unknown_mitigation"})
    )

    assert data["mitigation_applied"] is None
    assert data["base_impact"] == data["mitigated_impact"]


def test_simulation_what_if_unknown_scenario_returns_404():
    response = client.get("/api/s2p/simulation/what-if/UNKNOWN-SCENARIO")

    assert response.status_code == 404
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)


def test_simulation_impact_summary_returns_totals():
    data = assert_dict_response(client.get("/api/s2p/simulation/impact-summary"))

    assert data["total_scenarios"] >= 0
    assert "total_quarterly_exposure" in data
