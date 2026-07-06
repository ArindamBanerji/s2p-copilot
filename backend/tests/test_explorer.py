from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer


client = TestClient(app)

VALID_REQUEST = {
    "event_id": "S2P-EXP-001",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-001",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


@pytest.fixture(autouse=True)
def reset_scorer_state():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def test_centroid_returns_200():
    response = client.get("/api/s2p/explorer/centroid/price_variance/auto_approve")
    assert response.status_code == 200


def test_centroid_has_7_factors():
    response = client.get("/api/s2p/explorer/centroid/price_variance/auto_approve")
    payload = response.json()
    assert len(payload["centroid"]) == S2PDomainConfig.n_factors
    assert len(payload["factors"]) == S2PDomainConfig.n_factors


def test_centroid_values_bounded():
    response = client.get("/api/s2p/explorer/centroid/price_variance/auto_approve")
    assert all(0.0 <= value <= 1.0 for value in response.json()["centroid"])


def test_centroid_invalid_category_404():
    response = client.get("/api/s2p/explorer/centroid/not_a_category/auto_approve")
    assert response.status_code == 404


def test_all_valid_cells_accessible():
    for category in S2PDomainConfig.categories:
        for action in S2PDomainConfig.actions:
            response = client.get(f"/api/s2p/explorer/centroid/{category}/{action}")
            assert response.status_code == 200
            assert len(response.json()["centroid"]) == S2PDomainConfig.n_factors


def test_centroid_response_has_names():
    response = client.get("/api/s2p/explorer/centroid/price_variance/auto_approve")
    payload = response.json()
    assert payload["category"] == 0
    assert payload["category_name"] == "price_variance"
    assert payload["action"] == 0
    assert payload["action_name"] == "auto_approve"


def test_contribution_unknown_invoice_404():
    response = client.get("/api/s2p/explorer/contribution?invoice_id=NO-SUCH-INVOICE")
    assert response.status_code == 404
    assert "Score the invoice first" in response.json()["detail"]


def test_contribution_after_scoring_if_available():
    score = client.post("/api/s2p/score", json=VALID_REQUEST)
    assert score.status_code == 200

    response = client.get(
        f"/api/s2p/explorer/contribution?invoice_id={VALID_REQUEST['event_id']}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["invoice_id"] == VALID_REQUEST["event_id"]
    assert payload["category"] == VALID_REQUEST["category"]
    assert payload["scored_action"] == score.json()["action"]
    assert isinstance(payload["confidence"], (int, float))


def test_contribution_has_7_factors_if_available():
    score = client.post("/api/s2p/score", json=VALID_REQUEST)
    assert score.status_code == 200

    response = client.get(
        f"/api/s2p/explorer/contribution?invoice_id={VALID_REQUEST['event_id']}"
    )

    assert response.status_code == 200
    contributions = response.json()["contributions"]
    assert len(contributions) == S2PDomainConfig.n_factors
    assert [row["factor"] for row in contributions] == list(S2PDomainConfig.factors)
    assert {row["factor_index"] for row in contributions} == set(range(S2PDomainConfig.n_factors))
    for row in contributions:
        assert set(row["distance_to_actions"]) == set(S2PDomainConfig.actions)


def test_drift_returns_200():
    response = client.get("/api/s2p/explorer/drift/price_variance")
    assert response.status_code == 200


def test_drift_has_centroids_per_action():
    response = client.get("/api/s2p/explorer/drift/price_variance")
    payload = response.json()
    assert set(payload["centroids"]) == set(S2PDomainConfig.actions)
    for centroid in payload["centroids"].values():
        assert len(centroid) == S2PDomainConfig.n_factors


def test_dk_weights_returns_200():
    response = client.get("/api/s2p/explorer/dk-weights")
    assert response.status_code == 200


def test_dk_weights_has_factors_list():
    response = client.get("/api/s2p/explorer/dk-weights")
    payload = response.json()
    assert payload["factors"] == list(S2PDomainConfig.factors)
    assert payload["available"] is False
    assert payload["weights"] == []


def test_centroid_route_exists():
    paths = {route.path for route in app.routes}
    assert "/api/s2p/explorer/centroid/{category}/{action}" in paths


def test_drift_route_exists():
    paths = {route.path for route in app.routes}
    assert "/api/s2p/explorer/drift/{category}" in paths


def test_dk_route_exists():
    paths = {route.path for route in app.routes}
    assert "/api/s2p/explorer/dk-weights" in paths


def test_contribution_route_exists():
    paths = {route.path for route in app.routes}
    assert "/api/s2p/explorer/contribution" in paths
