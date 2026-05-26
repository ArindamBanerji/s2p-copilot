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


def assert_dict_response(path):
    response = client.get(path)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)
    return data


def test_discovery_alerts_returns_discoveries():
    data = assert_dict_response("/api/s2p/discovery/alerts")

    assert isinstance(data["discoveries"], list)
    assert data["total_discoveries"] == len(data["discoveries"])


def test_discovery_disruptions_returns_recovery_history():
    data = assert_dict_response("/api/s2p/discovery/disruptions")

    assert isinstance(data["disruptions"], list)
    assert data["total_disruptions"] == len(data["disruptions"])


def test_discovery_extended_returns_supplier_distribution():
    data = assert_dict_response("/api/s2p/discovery/extended")

    assert isinstance(data["discoveries"], list)
    assert isinstance(data["per_supplier"], dict)


def test_discovery_supplier_returns_known_supplier_rows():
    data = assert_dict_response("/api/s2p/discovery/supplier/SUP-YANGTZE")

    assert data["supplier_id"] == "SUP-YANGTZE"
    assert isinstance(data["discoveries"], list)


def test_discovery_supplier_unknown_returns_empty_success_payload():
    data = assert_dict_response("/api/s2p/discovery/supplier/UNKNOWN-SUPPLIER")

    assert data["supplier_id"] == "UNKNOWN-SUPPLIER"
    assert data["discoveries"] == []
    assert data["total"] == 0


def test_discovery_propagation_returns_known_discovery():
    data = assert_dict_response("/api/s2p/discovery/propagation/DISC-EXT-001")

    assert data["discovery_id"] == "DISC-EXT-001"
    assert data["propagation_path"]


def test_discovery_propagation_unknown_id_returns_404():
    response = client.get("/api/s2p/discovery/propagation/UNKNOWN-DISCOVERY")

    assert response.status_code == 404
