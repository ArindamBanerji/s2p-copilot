import json
import os
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402
from app.s2p_graph_status import (  # noqa: E402
    S2PActiveAGEGraphStore,
    S2PActiveGraphConfig,
    S2PActiveGraphConfigError,
    create_s2p_active_graph_store,
)
from app.s2p_shadow import initialize_s2p_shadow_state  # noqa: E402


PRODUCT_LIKE_ENV = {
    "S2P_ACTIVE_GRAPH_BACKEND": "age",
    "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:postgres@127.0.0.1/db?token=secret",
    "S2P_ACTIVE_AGE_GRAPH": "governed_copilot_graph",
    "S2P_ACTIVE_AGE_DOMAIN": "s2p",
    "S2P_ACTIVE_AGE_TEST_MODE": "0",
}


def _reset_app_state(active_config: S2PActiveGraphConfig | None = None) -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    app.state.s2p_shadow = initialize_s2p_shadow_state(env={})
    app.state.s2p_active_graph_config = active_config or S2PActiveGraphConfig.from_env({})
    s2p_router._clear_score_conservation_status_cache()


@pytest.fixture(autouse=True)
def reset_app_after_test():
    _reset_app_state()
    yield
    _reset_app_state()


def _assert_no_secret_text(value) -> None:
    encoded = json.dumps(value, sort_keys=True)
    assert "postgres:postgres@" not in encoded
    assert "token=secret" not in encoded
    assert "password=" not in encoded


def test_product_like_config_status_reports_guarded_intent_without_activation():
    config = S2PActiveGraphConfig.from_env(PRODUCT_LIKE_ENV)
    _reset_app_state(config)

    body = TestClient(app).get("/api/s2p/graph/status").json()

    assert body["requested_backend"] == "age"
    assert body["active_backend"] == "sqlite"
    assert body["sqlite_authoritative"] is True
    assert body["age_active"] is False
    assert body["active_graph_name"] == "governed_copilot_graph"
    assert body["age_graph_kind"] == "product"
    assert body["graph_kind"] == "product"
    assert body["product_graph_allowed"] is True
    assert body["active_domain"] == "s2p"
    assert body["active_test_mode"] is False
    assert body["migration_backfill_status"] == "not_in_scope"
    assert body["receipt_mapping_status"] == "excluded_first_cutover"
    assert body["evidence_receipt_mapping_status"] == "design_required"
    assert body["true_parallel_gate_status"] == "completed_backend_live"
    assert body["cutover_ready"] is False

    flags = body["cutover_ready_flags"]
    assert flags["product_graph_allow_listed"] is True
    assert flags["true_parallel_gate_complete"] is True
    assert flags["rollback_proof_complete"] is True
    assert flags["evidence_receipt_mapping_complete"] is False
    assert flags["migration_backfill_in_scope"] is False
    assert flags["active_age_writes_enabled"] is False
    assert flags["product_claim_allowed"] is False
    assert "Historical SQLite records are not visible" in body["historical_visibility_warning"]
    _assert_no_secret_text(body)


def test_product_like_store_construction_uses_factory_without_live_product_connection():
    config = S2PActiveGraphConfig.from_env(PRODUCT_LIKE_ENV)
    calls = []
    fake_store = object()

    def factory(**kwargs):
        calls.append(dict(kwargs))
        return fake_store

    active = create_s2p_active_graph_store(config, store_factory=factory)

    assert isinstance(active, S2PActiveAGEGraphStore)
    assert active.active_phase == "product_decision_outcome_cutover"
    assert calls == [
        {
            "backend": "age",
            "domain": "s2p",
            "dsn": PRODUCT_LIKE_ENV["S2P_ACTIVE_AGE_DSN"],
            "graph_name": "governed_copilot_graph",
            "env": {},
            "test_mode": False,
        }
    ]


@pytest.mark.parametrize(
    ("env_update", "match"),
    [
        ({"S2P_ACTIVE_AGE_GRAPH": "random_product_graph"}, "allow-listed"),
        ({"S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover"}, "S2P_ACTIVE_AGE_TEST_MODE"),
        ({"S2P_ACTIVE_AGE_GRAPH": "   "}, "S2P_ACTIVE_AGE_GRAPH"),
        ({"S2P_ACTIVE_AGE_DSN": ""}, "S2P_ACTIVE_AGE_DSN"),
    ],
)
def test_product_like_graph_guards_reject_unsafe_config(env_update, match):
    env = {**PRODUCT_LIKE_ENV, **env_update}

    with pytest.raises(S2PActiveGraphConfigError, match=match):
        S2PActiveGraphConfig.from_env(env)


def test_product_like_requires_dsn():
    env = dict(PRODUCT_LIKE_ENV)
    del env["S2P_ACTIVE_AGE_DSN"]

    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_DSN"):
        S2PActiveGraphConfig.from_env(env)


def test_product_like_rejects_test_mode_for_allow_listed_product_graph():
    env = {**PRODUCT_LIKE_ENV, "S2P_ACTIVE_AGE_TEST_MODE": "1"}

    with pytest.raises(S2PActiveGraphConfigError, match="protocol_v2_test"):
        S2PActiveGraphConfig.from_env(env)


def test_product_like_rejects_shadow_conflict():
    env = {**PRODUCT_LIKE_ENV, "S2P_SHADOW_AGE": "1"}

    with pytest.raises(S2PActiveGraphConfigError, match="S2P_SHADOW_AGE"):
        S2PActiveGraphConfig.from_env(env)


def test_generic_graph_env_does_not_switch_s2p_product_like_backend():
    config = S2PActiveGraphConfig.from_env(
        {
            "GRAPH_BACKEND": "age",
            "GRAPH_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "GRAPH_NAME": "governed_copilot_graph",
            "GRAPH_DOMAIN": "s2p",
        }
    )
    _reset_app_state(config)

    body = TestClient(app).get("/api/s2p/graph/status").json()

    assert body["requested_backend"] == "sqlite"
    assert body["active_backend"] == "sqlite"
    assert body["ignored_generic_graph_env"] is True
    assert body["active_graph_name"] is None
    _assert_no_secret_text(body)
