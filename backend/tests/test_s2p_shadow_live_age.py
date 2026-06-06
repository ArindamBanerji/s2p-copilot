import os
import sys
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402
from app.s2p_shadow import initialize_s2p_shadow_state  # noqa: E402


LIVE_ENV_KEYS = (
    "S2P_SHADOW_LIVE_AGE_TEST",
    "S2P_SHADOW_AGE",
    "S2P_AGE_DSN",
    "S2P_AGE_GRAPH",
    "S2P_AGE_TEST_MODE",
)


BASE_SCORE_BODY = {
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-SHADOW-LIVE",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def _live_age_env_present() -> bool:
    if os.environ.get("S2P_SHADOW_LIVE_AGE_TEST") != "1":
        return False
    return all(os.environ.get(key) for key in LIVE_ENV_KEYS[1:])


pytestmark = pytest.mark.skipif(
    not _live_age_env_present(),
    reason="set S2P_SHADOW_LIVE_AGE_TEST=1 and S2P AGE env vars for live AGE shadow tests",
)


def _reset_app_with_live_shadow():
    shadow = initialize_s2p_shadow_state()
    assert shadow.config.enabled is True
    assert shadow.config.strict is False
    assert shadow.config.graph != "soc_graph"
    assert str(shadow.config.graph).startswith("protocol_v2_test")
    assert shadow.config.test_mode is True
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    app.state.s2p_shadow = shadow
    s2p_router._clear_score_conservation_status_cache()
    return shadow


def _score(client: TestClient, suffix: str):
    return client.post(
        "/api/s2p/score",
        json={
            **BASE_SCORE_BODY,
            "event_id": f"S2P-SHADOW-LIVE-{suffix}-{uuid4()}",
        },
    )


def _latest_event(shadow, operation: str):
    events = [event for event in shadow.diagnostics.events() if event.operation == operation]
    assert events, f"missing {operation} diagnostic event"
    return events[-1]


def _metadata(node: dict, key: str = "metadata") -> dict:
    value = node.get(key)
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        assert isinstance(parsed, dict)
        return parsed
    return {}


def _assert_no_secret_text(value) -> None:
    encoded = json.dumps(value, sort_keys=True)
    assert "postgres:postgres@" not in encoded
    assert "password=" not in encoded.lower()
    assert "S2P_AGE_DSN" not in encoded


def _verified_decision(shadow, decision_id: str) -> dict:
    linked = [
        item
        for item in shadow.store.get_verified_decisions("s2p")
        if item.get("decision_id") == decision_id
    ]
    assert linked
    return linked[-1]


def test_live_age_shadow_score_non_strict_success():
    shadow = _reset_app_with_live_shadow()
    client = TestClient(app)

    response = _score(client, "SCORE")

    assert response.status_code == 200
    body = response.json()
    assert "shadow" not in body
    decision = shadow.store.get_decision(body["decision_id"])
    assert decision is not None
    assert decision["decision_id"] == body["decision_id"]
    assert decision["domain"] == "s2p"
    assert decision["status"] == "pending"
    metadata = _metadata(decision)
    assert metadata["shadow_run_id"] == shadow.diagnostics.shadow_run_id
    assert metadata["shadow_operation"] == "score_shadow"
    assert metadata["operation_id"] == body["decision_id"]
    _assert_no_secret_text(metadata)
    event = _latest_event(shadow, "score_shadow")
    assert event.status == "succeeded"
    assert event.operation_id == body["decision_id"]
    assert event.parity["decision_id_match"] is True
    assert event.parity["status_match"] is True
    _assert_no_secret_text(event.__dict__)


def test_live_age_shadow_outcome_non_strict_success():
    shadow = _reset_app_with_live_shadow()
    client = TestClient(app)
    verified_before = shadow.store.count_verified_decisions("s2p")
    score_response = _score(client, "OUTCOME")
    score = score_response.json()

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "pytest",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 200
    decision = shadow.store.get_decision(score["decision_id"])
    assert decision is not None
    assert decision["decision_id"] == score["decision_id"]
    assert decision["status"] == "confirmed"
    verified = _verified_decision(shadow, score["decision_id"])
    outcome_metadata = _metadata(verified, "outcome_metadata")
    assert outcome_metadata["shadow_run_id"] == shadow.diagnostics.shadow_run_id
    assert outcome_metadata["shadow_operation"] == "outcome_shadow"
    assert outcome_metadata["operation_id"] == score["decision_id"]
    _assert_no_secret_text(outcome_metadata)
    assert shadow.store.count_verified_decisions("s2p") >= verified_before + 1
    event = _latest_event(shadow, "outcome_shadow")
    assert event.status == "succeeded"
    assert event.operation_id == score["decision_id"]
    assert event.parity["decision_id_match"] is True
    assert event.parity["outcome_match"] is True
    _assert_no_secret_text(event.__dict__)

    duplicate = TestClient(app, raise_server_exceptions=False).post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "pytest",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )
    assert duplicate.status_code != 200


def test_live_age_shadow_learn_non_strict_success():
    shadow = _reset_app_with_live_shadow()
    client = TestClient(app)
    verified_before = shadow.store.count_verified_decisions("s2p")
    score_response = _score(client, "LEARN")
    score = score_response.json()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 200
    decision = shadow.store.get_decision(score["decision_id"])
    assert decision is not None
    assert decision["decision_id"] == score["decision_id"]
    assert decision["status"] == "confirmed"
    verified = _verified_decision(shadow, score["decision_id"])
    outcome_metadata = _metadata(verified, "outcome_metadata")
    assert outcome_metadata["shadow_run_id"] == shadow.diagnostics.shadow_run_id
    assert outcome_metadata["shadow_operation"] == "learn_shadow"
    assert outcome_metadata["operation_id"] == score["decision_id"]
    _assert_no_secret_text(outcome_metadata)
    assert shadow.store.count_verified_decisions("s2p") >= verified_before + 1
    event = _latest_event(shadow, "learn_shadow")
    assert event.status == "succeeded"
    assert event.operation_id == score["decision_id"]
    assert event.parity["decision_id_match"] is True
    assert event.parity["outcome_match"] is True

    duplicate = TestClient(app, raise_server_exceptions=False).post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )
    assert duplicate.status_code != 200


def test_live_age_shadow_preview_no_decision_write():
    shadow = _reset_app_with_live_shadow()
    client = TestClient(app)
    count_before = shadow.store.count_decisions("s2p")

    response = client.get("/api/s2p/preview/queue")

    assert response.status_code == 200
    assert shadow.store.count_decisions("s2p") == count_before
    assert not [
        event for event in shadow.diagnostics.events() if event.operation == "score_shadow"
    ]
