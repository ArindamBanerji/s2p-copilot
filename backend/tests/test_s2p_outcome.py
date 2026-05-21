"""
tests/test_s2p_outcome.py — POST /api/s2p/outcome endpoint tests.

Run from backend/:
    pytest tests/test_s2p_outcome.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app, build_s2p_scorer

client = TestClient(app)

BASE = {
    "decision_id":      "S2P-E001-2026-01-01T00-00-00",
    "outcome":          "confirm",
    "analyst_action":   "auto_approve",
    "analyst_id":       "A001",
    "factor_vector":    [0.9, 0.08, 0.04, 0.05, 0.48, 0.76, 0.90],
    "category":         "price_variance",
    "predicted_action": "auto_approve",
}


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def test_outcome_endpoint_confirm_returns_200():
    reset_sdk_scorer()
    response = client.post("/api/s2p/outcome", json=BASE)
    assert response.status_code == 200
    assert response.json()["outcome"] == "confirm"


def test_outcome_endpoint_override_returns_200():
    reset_sdk_scorer()
    payload = {**BASE, "outcome": "override", "analyst_action": "hold_for_review",
               "predicted_action": "auto_approve", "reason_code": "wrong_action"}
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 200


def test_learning_disabled_by_default():
    reset_sdk_scorer()
    response = client.post("/api/s2p/outcome", json=BASE)
    assert response.json()["learning_applied"] == True
    assert "reward" in response.json()


def test_invalid_outcome_returns_422():
    payload = {**BASE, "outcome": "approve"}   # not "confirm" or "override"
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 422


def test_invalid_analyst_action_returns_422():
    payload = {**BASE, "analyst_action": "suppress"}   # SOC action
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 422


def test_legacy_analyst_action_returns_422():
    payload = {**BASE, "analyst_action": "approve"}
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 422


def test_invalid_factor_vector_length_returns_422():
    payload = {**BASE, "factor_vector": [0.5] * 4}
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 422


def test_reason_code_accepted_on_override():
    reset_sdk_scorer()
    payload = {**BASE, "outcome": "override", "analyst_action": "hold_for_review", "reason_code": "wrong_action"}
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 200
    assert response.json()["reason_code"] == "wrong_action"


def test_reason_code_required_on_override():
    reset_sdk_scorer()
    payload = {**BASE, "outcome": "override", "analyst_action": "hold_for_review"}
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 400
    assert "reason_code" in response.json()["detail"]


def test_reason_code_optional_on_confirm():
    reset_sdk_scorer()
    response = client.post("/api/s2p/outcome", json=BASE)
    assert response.status_code == 200
    assert response.json()["outcome"] == "confirm"


def test_reason_code_rejected_invalid():
    reset_sdk_scorer()
    payload = {**BASE, "outcome": "override", "analyst_action": "hold_for_review", "reason_code": "not_allowed"}
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 400
    assert "reason_code" in response.json()["detail"]


def test_reason_code_stored_in_outcome_metadata():
    reset_sdk_scorer()
    score_payload = {
        "event_id": "REASON-CODE-001",
        "category": "price_variance",
        "amount": 1000.0,
        "supplier_id": "SUP-RC",
        "match_status": 0.9,
        "amount_variance_ratio": 0.2,
        "duplicate_score": 0.1,
        "supplier_exception_history": 0.1,
        "payment_terms_impact": 0.2,
        "commodity_index_correlation": 0.3,
        "tax_regulatory_compliance": 0.9,
    }
    score = client.post("/api/s2p/score", json=score_payload).json()
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "override",
            "analyst_action": "hold_for_review",
            "analyst_id": "A001",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
            "reason_code": "missing_context",
        },
    )
    assert response.status_code == 200
    verified = app.state.scorer.graph_store.get_verified_decisions()
    matching = [row for row in verified if row["decision_id"] == score["decision_id"]]
    assert matching
    assert matching[0]["outcome_metadata"]["context"]["reason_code"] == "missing_context"
