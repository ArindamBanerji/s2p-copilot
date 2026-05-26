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


def test_governance_compliance_screening_returns_summary():
    data = assert_dict_response("/api/s2p/governance/compliance-screening")

    assert "total_decisions_screened" in data
    assert "gaps" in data


def test_governance_compliance_gaps_returns_issue_summary():
    data = assert_dict_response("/api/s2p/governance/compliance-gaps")

    assert "total_gaps" in data
    assert isinstance(data["issue_summary"], dict)


def test_governance_conservation_proof_returns_current_state():
    data = assert_dict_response("/api/s2p/governance/conservation-proof")

    assert "current_state" in data
    assert "proof_complete" in data


def test_governance_rationalization_returns_recommendations():
    data = assert_dict_response("/api/s2p/governance/rationalization")

    assert data["total_suppliers"] >= 0
    assert isinstance(data["recommendations"], list)


def test_governance_rationalization_overlap_returns_groups():
    data = assert_dict_response("/api/s2p/governance/rationalization/overlap")

    assert isinstance(data["overlap_groups"], list)
    assert data["total_groups"] == len(data["overlap_groups"])


def test_governance_rationalization_supplier_returns_known_supplier():
    data = assert_dict_response("/api/s2p/governance/rationalization/supplier/SUP-001")

    assert data["supplier"]["supplier_id"] == "SUP-001"
    assert data["recommendation"]["supplier_id"] == "SUP-001"


def test_governance_rationalization_supplier_unknown_returns_404():
    response = client.get("/api/s2p/governance/rationalization/supplier/UNKNOWN-SUPPLIER")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]
