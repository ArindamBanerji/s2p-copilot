import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer


client = TestClient(app)

SCORE_BODY = {
    "event_id": "ROUTER-CORE-001",
    "category": "duplicate_risk",
    "amount": 1250.0,
    "supplier_id": "SUP-001",
    "match_status": 0.8,
    "amount_variance_ratio": 0.3,
    "duplicate_score": 0.9,
    "supplier_exception_history": 0.2,
    "payment_terms_impact": 0.5,
    "commodity_index_correlation": 0.4,
    "tax_regulatory_compliance": 0.7,
    "environmental_risk": 0.5,
}

FACTOR_NAMES = S2PDomainConfig.factors


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


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def score_event(event_id="ROUTER-CORE-001"):
    reset_sdk_scorer()
    response = client.post("/api/s2p/score", json={**SCORE_BODY, "event_id": event_id})
    data = assert_dict_response(response)
    return data


def test_core_score_accepts_all_factor_names():
    data = score_event("ROUTER-CORE-SCORE")

    assert data["category"] == "duplicate_risk"
    assert data["factor_names"] == FACTOR_NAMES
    assert len(data["factor_vector"]) == S2PDomainConfig.n_factors
    assert set(FACTOR_NAMES) == set(SCORE_BODY) & set(FACTOR_NAMES)


def test_core_score_response_contains_decision_fields():
    data = score_event("ROUTER-CORE-DECISION")

    assert data["decision_id"]
    assert data["action"]
    assert isinstance(data["probabilities"], list)
    assert len(data["probabilities"]) == 5


def test_core_score_rejects_unknown_category():
    response = client.post("/api/s2p/score", json={**SCORE_BODY, "category": "match_status_variance"})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_core_score_get_method_not_allowed():
    response = client.get("/api/s2p/score")

    assert response.status_code == 405


def test_core_score_then_outcome_confirm_flow():
    score = score_event("ROUTER-CORE-FLOW")

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "A-ROUTER",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    data = assert_dict_response(response)
    assert data["outcome"] == "confirm"
    assert data["learning_applied"] in {True, False}


def test_core_outcome_invalid_decision_id_returns_404():
    reset_sdk_scorer()
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": "UNKNOWN-DECISION-ID",
            "outcome": "confirm",
            "analyst_action": "auto_approve",
            "analyst_id": "A-ROUTER",
            "factor_vector": [0.5] * S2PDomainConfig.n_factors,
            "category": "duplicate_risk",
            "predicted_action": "auto_approve",
        },
    )

    assert response.status_code == 404
    assert "Unknown decision" in response.json()["detail"]


def test_core_outcome_override_requires_reason_code():
    score = score_event("ROUTER-CORE-REASON")
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "override",
            "analyst_action": "hold_for_review",
            "analyst_id": "A-ROUTER",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 400
    assert "reason_code" in response.json()["detail"]


def test_core_outcome_rejects_invalid_outcome_value():
    score = score_event("ROUTER-CORE-BAD-OUTCOME")
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "maybe",
            "analyst_action": score["action"],
            "analyst_id": "A-ROUTER",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 422
    assert "outcome must be" in response.json()["detail"]


def test_core_outcome_rejects_invalid_analyst_action():
    score = score_event("ROUTER-CORE-BAD-ACTION")
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": "not_a_s2p_action",
            "analyst_id": "A-ROUTER",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 422
    assert "analyst_action must be one of" in response.json()["detail"]


def test_core_outcome_rejects_wrong_factor_vector_length():
    score = score_event("ROUTER-CORE-BAD-VECTOR")
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "A-ROUTER",
            "factor_vector": score["factor_vector"][:6],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 422
    assert f"factor_vector must contain {S2PDomainConfig.n_factors} values" in response.json()["detail"]


def test_core_learn_after_score_uses_api_learn_path():
    score = score_event("ROUTER-CORE-LEARN")

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirm",
        },
    )

    assert_dict_response(response)


def test_core_learn_unknown_decision_returns_404():
    reset_sdk_scorer()
    response = client.post(
        "/api/learn",
        json={"decision_id": "UNKNOWN-LEARN-ID", "actual_action": "auto_approve"},
    )

    assert response.status_code == 404


def test_core_learn_rejects_invalid_actual_action():
    score = score_event("ROUTER-CORE-BAD-LEARN")
    response = client.post(
        "/api/learn",
        json={"decision_id": score["decision_id"], "actual_action": "not_a_s2p_action"},
    )

    assert response.status_code == 422
    assert "actual_action must be one of" in response.json()["detail"]


def test_core_auto_approve_endpoints_return_json_safe_dicts():
    stats = assert_dict_response(client.get("/api/s2p/auto-approve/stats"))
    proof = assert_dict_response(
        client.get("/api/s2p/auto-approve/expansion-proof", params={"category": "price_variance"})
    )

    assert isinstance(stats, dict)
    assert proof["category"] == "price_variance"


def test_core_auto_approve_expansion_proof_unknown_category_returns_404():
    response = client.get("/api/s2p/auto-approve/expansion-proof", params={"category": "not_a_s2p_category"})

    assert response.status_code == 404
    assert "Unknown category" in response.json()["detail"]


def test_core_status_endpoints_return_json_safe_dicts():
    reset_sdk_scorer()
    iks = assert_dict_response(client.get("/api/s2p/iks"))
    gate = assert_dict_response(client.get("/api/s2p/learning-gate"))

    assert isinstance(iks, dict)
    for key in ("iks", "d_max", "mean_drift", "decisions", "domain", "status", "learning_active", "interpretation"):
        assert key in iks
    assert iks["domain"] == "s2p"
    assert "status" in gate
