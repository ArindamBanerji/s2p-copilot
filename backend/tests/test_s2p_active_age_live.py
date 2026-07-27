import os
import sys
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402
from app.s2p_graph_status import (  # noqa: E402
    S2PActiveAGEGraphStore,
    S2PActiveGraphConfig,
    create_s2p_active_graph_store,
)
from app.s2p_shadow import initialize_s2p_shadow_state  # noqa: E402


BASE_SCORE_BODY = {
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-ACTIVE-LIVE",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def _reset_app_with_live_active_age(s2p_age_test_env) -> S2PActiveAGEGraphStore:
    config = S2PActiveGraphConfig.from_env(s2p_age_test_env.active)
    assert config.requested_backend == "age"
    assert config.domain == "s2p"
    assert config.graph != "soc_graph"
    assert str(config.graph).startswith("protocol_v2_test")
    assert config.test_mode is True
    active_store = create_s2p_active_graph_store(config)
    assert isinstance(active_store, S2PActiveAGEGraphStore)
    app.state.s2p_active_graph_config = config
    app.state.scorer = build_s2p_scorer(graph_store=active_store)
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    app.state.s2p_shadow = initialize_s2p_shadow_state(env={})
    s2p_router._clear_score_conservation_status_cache()
    return active_store


def _reset_app_with_sqlite() -> None:
    app.state.s2p_active_graph_config = S2PActiveGraphConfig.from_env({})
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    app.state.s2p_shadow = initialize_s2p_shadow_state(env={})
    s2p_router._clear_score_conservation_status_cache()


def _score(client: TestClient, suffix: str):
    return client.post(
        "/api/s2p/score",
        json={
            **BASE_SCORE_BODY,
            "event_id": f"S2P-ACTIVE-LIVE-{suffix}-{uuid4()}",
        },
    )


def test_live_active_age_score_test_mode_success(s2p_age_test_env):
    active_store = _reset_app_with_live_active_age(s2p_age_test_env)
    client = TestClient(app)

    response = _score(client, "SCORE")

    assert response.status_code == 200
    body = response.json()
    decision = active_store.get_decision(body["decision_id"])
    assert decision is not None
    assert decision["decision_id"] == body["decision_id"]
    assert decision["domain"] == "s2p"
    assert decision["status"] == "pending"
    assert decision["metadata"]["active_age"] is True
    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "age"
    assert status["age_active"] is True
    assert status["sqlite_authoritative"] is False
    assert "postgres:postgres@" not in str(status)


def test_live_active_age_outcome_test_mode_success_and_invariant(s2p_age_test_env):
    active_store = _reset_app_with_live_active_age(s2p_age_test_env)
    client = TestClient(app)
    score = _score(client, "OUTCOME").json()

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
    decision = active_store.get_decision(score["decision_id"])
    assert decision is not None
    assert decision["status"] == "confirmed"
    verified = [
        item
        for item in active_store.get_verified_decisions("s2p")
        if item.get("decision_id") == score["decision_id"]
    ]
    assert verified
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


def test_live_active_age_learn_test_mode_success_and_invariant(s2p_age_test_env):
    active_store = _reset_app_with_live_active_age(s2p_age_test_env)
    client = TestClient(app)
    score = _score(client, "LEARN").json()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 200
    decision = active_store.get_decision(score["decision_id"])
    assert decision is not None
    assert decision["decision_id"] == score["decision_id"]
    assert decision["domain"] == "s2p"
    assert decision["status"] == "confirmed"
    verified = [
        item
        for item in active_store.get_verified_decisions("s2p")
        if item.get("decision_id") == score["decision_id"]
    ]
    assert verified
    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "age"
    assert status["age_active"] is True
    assert status["active_graph_name"] != "soc_graph"

    duplicate = TestClient(app, raise_server_exceptions=False).post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )
    assert duplicate.status_code != 200


def test_live_active_age_preview_no_decision_write(s2p_age_test_env):
    active_store = _reset_app_with_live_active_age(s2p_age_test_env)
    client = TestClient(app)
    before = active_store.count_decisions("s2p")

    response = client.get("/api/s2p/preview/queue")

    assert response.status_code == 200
    assert active_store.count_decisions("s2p") == before


def test_live_active_age_rollback_to_sqlite(s2p_age_test_env):
    active_store = _reset_app_with_live_active_age(s2p_age_test_env)
    client = TestClient(app)
    response = _score(client, "ROLLBACK-ACTIVE")
    assert response.status_code == 200
    assert active_store.get_decision(response.json()["decision_id"]) is not None

    _reset_app_with_sqlite()
    before = app.state.graph_store.count_decisions("s2p")
    sqlite_response = _score(client, "ROLLBACK-SQLITE")

    assert sqlite_response.status_code == 200
    assert app.state.graph_store.count_decisions("s2p") == before + 1
    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "sqlite"
    assert status["sqlite_authoritative"] is True
