import json
import os
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402
from app.s2p_graph_status import (  # noqa: E402
    S2PActiveGraphConfig,
    S2PActiveGraphConfigError,
)
from copilot_sdk.config import GraphConfigError  # noqa: E402
from app.s2p_shadow import initialize_s2p_shadow_state  # noqa: E402


VALID_SCORE_REQUEST = {
    "event_id": "S2P-CUTOVER-PHASE-A",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-CUTOVER-A",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
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
    assert "password=abc" not in encoded
    assert "secret" not in encoded.lower()


def test_active_graph_config_defaults_to_sqlite_without_age_requirements():
    config = S2PActiveGraphConfig.from_env({})

    assert config.requested_backend == "sqlite"
    assert config.domain == "s2p"
    assert config.dsn is None
    assert config.graph is None
    assert config.test_mode is False


def test_production_config_fails_closed_when_age_dsn_is_missing(monkeypatch):
    """Production resolution must surface the loader's missing-DSN error."""
    for name in (
        "GRAPH_CONFIG_PATH",
        "S2P_ACTIVE_GRAPH_BACKEND",
        "S2P_ACTIVE_AGE_DSN",
        "S2P_ACTIVE_AGE_DOMAIN",
        "S2P_ACTIVE_AGE_TEST_MODE",
        "S2P_SHADOW_AGE",
        "S2P_ACTIVE_LIVE_AGE_TEST",
        "GRAPH_DSN",
        "AGE_DSN",
        "S2P_ACTIVE_AGE_GRAPH",
        "GRAPH_NAME",
        "AGE_GRAPH_NAME",
        "GRAPH_DOMAIN",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(S2PActiveGraphConfigError) as exc_info:
        S2PActiveGraphConfig.from_env()

    assert isinstance(exc_info.value.__cause__, GraphConfigError)
    assert "missing AGE DSN" in str(exc_info.value)


def test_active_graph_config_rejects_invalid_backend():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_GRAPH_BACKEND"):
        S2PActiveGraphConfig.from_env({"S2P_ACTIVE_GRAPH_BACKEND": "postgres"})


def test_active_age_rejects_missing_dsn():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_DSN"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover",
                "S2P_ACTIVE_AGE_TEST_MODE": "1",
            }
        )


def test_active_age_rejects_missing_graph():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_GRAPH"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
                "S2P_ACTIVE_AGE_TEST_MODE": "1",
            }
        )


def test_active_age_rejects_blank_graph():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_GRAPH"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
                "S2P_ACTIVE_AGE_GRAPH": "   ",
                "S2P_ACTIVE_AGE_TEST_MODE": "1",
            }
        )


def test_active_age_rejects_blank_domain():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_DOMAIN"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DOMAIN": "   ",
            }
        )


def test_graph_status_default_reports_sqlite_authoritative():
    response = TestClient(app).get("/api/s2p/graph/status")

    assert response.status_code == 200
    body = response.json()
    assert body["active_backend"] == "sqlite"
    assert body["requested_backend"] == "sqlite"
    assert body["sqlite_authoritative"] is True
    assert body["age_active"] is False
    assert body["active_domain"] == "s2p"
    assert body["migration_backfill_status"] == "not_in_scope"
    assert body["receipt_mapping_status"] == "excluded_first_cutover"
    assert body["cutover_ready"] is False
    _assert_no_secret_text(body)


def test_generic_graph_env_is_ignored_for_s2p_active_backend():
    config = S2PActiveGraphConfig.from_env(
        {
            "GRAPH_BACKEND": "age",
            "GRAPH_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "GRAPH_NAME": "soc_graph",
            "GRAPH_DOMAIN": "soc",
        }
    )
    _reset_app_state(config)

    response = TestClient(app).get("/api/s2p/graph/status")

    assert response.status_code == 200
    body = response.json()
    assert body["active_backend"] == "sqlite"
    assert body["requested_backend"] == "sqlite"
    assert body["ignored_generic_graph_env"] is True
    assert body["active_graph_name"] is None
    _assert_no_secret_text(body)


def test_active_age_config_validates_test_graph_without_constructing_age_store():
    config = S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover",
            "S2P_ACTIVE_AGE_TEST_MODE": "1",
        }
    )
    _reset_app_state(config)

    response = TestClient(app).get("/api/s2p/graph/status")

    assert response.status_code == 200
    body = response.json()
    assert body["active_backend"] == "sqlite"
    assert body["requested_backend"] == "age"
    assert body["age_active"] is False
    assert body["shadow_allowed"] is False
    assert body["active_graph_name"] == "protocol_v2_test_cutover"
    assert body["age_graph_kind"] == "test"
    assert body["active_test_mode"] is True
    assert "AGE" not in type(app.state.graph_store).__name__
    _assert_no_secret_text(body)


def test_product_graph_allow_list_accepts_governed_copilot_graph_without_constructing_store():
    config = S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_ACTIVE_AGE_GRAPH": "governed_copilot_graph",
            "S2P_ACTIVE_AGE_DOMAIN": "s2p",
            "S2P_ACTIVE_AGE_TEST_MODE": "0",
        }
    )

    assert config.graph_kind() == "product"
    assert config.graph == "governed_copilot_graph"
    assert config.test_mode is False


def test_product_graph_rejects_non_allow_listed_graph():
    with pytest.raises(S2PActiveGraphConfigError, match="allow-listed"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
                "S2P_ACTIVE_AGE_GRAPH": "random_product_graph",
                "S2P_ACTIVE_AGE_DOMAIN": "s2p",
                "S2P_ACTIVE_AGE_TEST_MODE": "0",
            }
        )


def test_product_mode_rejects_protocol_v2_test_graph():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_TEST_MODE"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
                "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover",
                "S2P_ACTIVE_AGE_DOMAIN": "s2p",
                "S2P_ACTIVE_AGE_TEST_MODE": "0",
            }
        )


def test_test_mode_rejects_product_graph():
    with pytest.raises(S2PActiveGraphConfigError, match="protocol_v2_test"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
                "S2P_ACTIVE_AGE_GRAPH": "governed_copilot_graph",
                "S2P_ACTIVE_AGE_DOMAIN": "s2p",
                "S2P_ACTIVE_AGE_TEST_MODE": "1",
            }
        )


def test_product_graph_status_reports_readiness_fields_without_cutover():
    config = S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:postgres@127.0.0.1/db?token=secret",
            "S2P_ACTIVE_AGE_GRAPH": "governed_copilot_graph",
            "S2P_ACTIVE_AGE_DOMAIN": "s2p",
            "S2P_ACTIVE_AGE_TEST_MODE": "0",
        }
    )
    _reset_app_state(config)

    body = TestClient(app).get("/api/s2p/graph/status").json()

    assert body["active_backend"] == "sqlite"
    assert body["requested_backend"] == "age"
    assert body["age_active"] is False
    assert body["active_graph_name"] == "governed_copilot_graph"
    assert body["age_graph_kind"] == "product"
    assert body["graph_kind"] == "product"
    assert body["product_graph_allowed"] is True
    assert "governed_copilot_graph" in body["product_graph_allow_list"]
    assert body["product_cutover_implementation_ready"] is False
    assert body["true_parallel_gate_status"] == "completed_backend_live"
    assert body["evidence_receipt_mapping_status"] == "design_required"
    assert body["receipt_mapping_status"] == "excluded_first_cutover"
    assert body["migration_backfill_status"] == "not_in_scope"
    assert "Historical SQLite records are not visible" in body["historical_visibility_warning"]
    flags = body["cutover_ready_flags"]
    assert flags["product_graph_allow_listed"] is True
    assert flags["true_parallel_gate_complete"] is True
    assert flags["rollback_proof_complete"] is True
    assert flags["evidence_receipt_mapping_complete"] is False
    assert flags["migration_backfill_in_scope"] is False
    assert flags["product_claim_allowed"] is False
    _assert_no_secret_text(body)


def test_active_age_derives_soc_graph_authorization():
    config = S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_ACTIVE_AGE_GRAPH": "soc_graph",
        }
    )
    assert config.shared_graph_authorization == "s2p:soc_graph"


def test_active_age_protocol_v2_test_requires_test_mode():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_TEST_MODE"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
                "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover",
            }
        )


def test_active_age_rejects_shadow_self_conflict():
    with pytest.raises(S2PActiveGraphConfigError, match="S2P_SHADOW_AGE"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
                "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover",
                "S2P_ACTIVE_AGE_TEST_MODE": "1",
                "S2P_SHADOW_AGE": "1",
            }
        )


def test_active_age_domain_locked_to_s2p():
    with pytest.raises(S2PActiveGraphConfigError, match="s2p"):
        S2PActiveGraphConfig.from_env(
            {
                "S2P_ACTIVE_GRAPH_BACKEND": "age",
                "S2P_ACTIVE_AGE_DOMAIN": "trading",
            }
        )


def test_direct_constructed_active_age_config_rejects_non_s2p_domain():
    config = S2PActiveGraphConfig(
        requested_backend="age",
        dsn="postgresql://postgres:secret@127.0.0.1/db",
        graph="protocol_v2_test_cutover",
        domain="trading",
        test_mode=True,
    )

    with pytest.raises(S2PActiveGraphConfigError, match="S2P_ACTIVE_AGE_DOMAIN"):
        config.validate()


def test_direct_constructed_active_age_config_accepts_s2p_domain():
    config = S2PActiveGraphConfig(
        requested_backend="age",
        dsn="postgresql://postgres:secret@127.0.0.1/db",
        graph="protocol_v2_test_cutover",
        domain="s2p",
        test_mode=True,
    )

    config.validate()


def test_status_redacts_dsn_and_reports_rollback_instructions():
    config = S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:postgres@127.0.0.1/db?password=abc",
            "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover",
            "S2P_ACTIVE_AGE_TEST_MODE": "1",
        }
    )
    _reset_app_state(config)

    body = TestClient(app).get("/api/s2p/graph/status").json()

    assert body["rollback_instructions"]
    assert body["diagnostics_summary"]["recent_events_exposed"] is False
    _assert_no_secret_text(body)


def test_phase_a_active_age_intent_does_not_switch_score_writes_off_sqlite():
    config = S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover",
            "S2P_ACTIVE_AGE_TEST_MODE": "1",
        }
    )
    _reset_app_state(config)
    before = app.state.graph_store.count_decisions("s2p")

    response = TestClient(app).post("/api/s2p/score", json=VALID_SCORE_REQUEST)

    assert response.status_code == 200
    assert app.state.graph_store.count_decisions("s2p") == before + 1
    assert "AGE" not in type(app.state.graph_store).__name__
    status = TestClient(app).get("/api/s2p/graph/status").json()
    assert status["requested_backend"] == "age"
    assert status["active_backend"] == "sqlite"
    assert status["age_active"] is False
