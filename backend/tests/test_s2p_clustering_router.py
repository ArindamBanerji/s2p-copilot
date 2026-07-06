import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.services.supplier_profile_accumulator import accumulator


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


@pytest.fixture(autouse=True)
def reset_supplier_profiles():
    accumulator.reset()
    yield
    accumulator.reset()


def seed_supplier(supplier_id="SUP-001"):
    accumulator.on_decision_verified(
        {
            "category": "price_variance",
            "recommended_action": "auto_approve",
            "metadata": {
                "supplier_id": supplier_id,
                "supplier_name": f"{supplier_id} Supplier",
                "invoice_id": f"INV-{supplier_id}",
                "invoice_date": "2026-01-01",
                "amount": 1000.0,
            },
        },
        {"actual_action": "auto_approve", "is_correct": True, "reward": 1.0},
        {},
    )


def test_clustering_clusters_returns_valid_payload_with_seeded_supplier():
    seed_supplier()
    data = assert_dict_response(client.get("/api/s2p/suppliers/clusters"))

    assert isinstance(data["clusters"], list)
    assert data["total_suppliers"] >= 1


def test_clustering_similarity_returns_nearest_rows_for_known_supplier():
    seed_supplier("SUP-001")
    seed_supplier("SUP-002")
    data = assert_dict_response(client.get("/api/s2p/suppliers/similarity", params={"supplier_id": "SUP-001"}))

    assert data["supplier_id"] == "SUP-001"
    assert isinstance(data["similar_suppliers"], list)


def test_clustering_similarity_unknown_supplier_returns_empty_rows():
    seed_supplier("SUP-001")
    data = assert_dict_response(client.get("/api/s2p/suppliers/similarity", params={"supplier_id": "UNKNOWN"}))

    assert data["supplier_id"] == "UNKNOWN"
    assert data["similar_suppliers"] == []
    assert "narrative" in data
