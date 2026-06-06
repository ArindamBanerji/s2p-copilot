from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from app.main import app
from app.services.s2p_evolution_dimensions import S2P_EVOLUTION_DIMENSIONS
from app.services.s2p_evolver import get_dimensions, propose_variant, shadow_test_variant


client = TestClient(app)


def test_dimension_definitions_have_required_fields():
    rows = get_dimensions()

    assert len(rows) >= 5
    for row in rows:
        assert {"name", "parameter_path", "search_space", "metric", "shadow_batch_size", "min_shadow_batches"} <= set(row)


def test_dimension_names_unique_and_search_spaces_valid():
    names = [dimension.name for dimension in S2P_EVOLUTION_DIMENSIONS]

    assert len(names) == len(set(names))
    for dimension in S2P_EVOLUTION_DIMENSIONS:
        low, high, step = dimension.search_space
        assert low < high
        assert step > 0


def test_dimension_min_shadow_batches_at_least_three():
    assert all(dimension.min_shadow_batches >= 3 for dimension in S2P_EVOLUTION_DIMENSIONS)


def test_expected_g13_dimensions_present():
    names = {dimension.name for dimension in S2P_EVOLUTION_DIMENSIONS}

    assert {
        "auto_approve_threshold_price_variance",
        "auto_approve_threshold_quantity_mismatch",
        "supplier_trust_weight",
        "factor_importance_price_variance",
        "escalation_amount_threshold",
        "batch_processing_threshold",
    } <= names


def test_propose_known_dimension_returns_variant_in_search_space():
    variant = propose_variant("auto_approve_threshold_price_variance")
    low, high, _step = variant["search_space"]

    assert variant["dimension"] == "auto_approve_threshold_price_variance"
    assert variant["variant_id"]
    assert low <= variant["proposed_value"] <= high
    assert variant["min_shadow_batches"] >= 3


def test_propose_unknown_dimension_returns_clean_error():
    result = propose_variant("missing_dimension")

    assert result["error"] == "unknown_dimension"
    assert "available_dimensions" in result


def test_dimensions_endpoint_returns_dimensions():
    response = client.get("/api/s2p/evolution/dimensions")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["dimensions"]) >= 5
    assert {"name", "search_space", "metric", "min_shadow_batches"} <= set(payload["dimensions"][0])


def test_propose_endpoint_known_dimension():
    response = client.post(
        "/api/s2p/evolution/propose",
        json={"dimension": "auto_approve_threshold_price_variance"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dimension"] == "auto_approve_threshold_price_variance"
    assert "variant_id" in payload
    assert "proposed_value" in payload


def test_propose_endpoint_missing_dimension_returns_400():
    response = client.post("/api/s2p/evolution/propose", json={})

    assert response.status_code == 400


def test_propose_endpoint_unknown_dimension_returns_clean_error():
    response = client.post("/api/s2p/evolution/propose", json={"dimension": "unknown"})

    assert response.status_code == 200
    assert response.json()["error"] == "unknown_dimension"


def test_existing_evolution_endpoints_still_work():
    for method, path in (
        ("get", "/api/s2p/evolution/rules"),
        ("get", "/api/s2p/evolution/variants"),
        ("get", "/api/s2p/evolution/promoted"),
        ("post", "/api/s2p/evolution/reset"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 200

    assert client.get("/api/s2p/evolution/rules").status_code == 200


def test_shadow_test_empty_decisions_returns_insufficient_data():
    variant = propose_variant("supplier_trust_weight")

    result = shadow_test_variant(variant, [])

    assert result["status"] == "insufficient_data"
    assert result["decisions_tested"] == 0
    assert result["read_only"] is True


def test_shadow_test_non_empty_decisions_completed_and_read_only():
    variant = propose_variant("supplier_trust_weight")
    decisions = [
        {
            "recommended_action": "hold_for_review",
            "actual_action": "hold_for_review",
        },
        {
            "recommended_action": "auto_approve",
            "actual_action": "hold_for_review",
        },
    ]
    before = deepcopy(decisions)

    result = shadow_test_variant(variant, decisions)

    assert result["status"] == "completed"
    assert result["decisions_tested"] == 2
    assert result["read_only"] is True
    assert decisions == before
