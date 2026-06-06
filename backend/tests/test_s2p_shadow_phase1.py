import os
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.s2p_shadow import (  # noqa: E402
    S2PShadowConfig,
    S2PShadowConfigError,
    S2PShadowDiagnostics,
    SHADOW_STATUSES,
)


VALID_SCORE_REQUEST = {
    "event_id": "S2P-SHADOW-PHASE1",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-SHADOW",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def test_shadow_config_defaults_disabled_without_dsn_or_graph():
    config = S2PShadowConfig.from_env({})

    assert config.enabled is False
    assert config.strict is False
    assert config.domain == "s2p"
    assert config.dsn is None
    assert config.graph is None


def test_shadow_config_no_arg_reads_os_environ(monkeypatch):
    monkeypatch.setenv("S2P_SHADOW_AGE", "1")
    monkeypatch.setenv(
        "S2P_AGE_DSN",
        "postgresql://postgres:secret@127.0.0.1:5433/soc_copilot",
    )
    monkeypatch.setenv("S2P_AGE_GRAPH", "protocol_v2_test_shadow")
    monkeypatch.setenv("S2P_AGE_TEST_MODE", "1")

    config = S2PShadowConfig.from_env()

    assert config.enabled is True
    assert config.graph == "protocol_v2_test_shadow"
    assert config.domain == "s2p"
    assert config.test_mode is True


def test_shadow_config_explicit_mapping_ignores_os_environ(monkeypatch):
    monkeypatch.setenv("S2P_SHADOW_AGE", "1")
    monkeypatch.setenv("S2P_AGE_DSN", "postgresql://postgres:secret@127.0.0.1/db")
    monkeypatch.setenv("S2P_AGE_GRAPH", "protocol_v2_test_from_environ")
    monkeypatch.setenv("S2P_AGE_TEST_MODE", "1")

    config = S2PShadowConfig.from_env({})

    assert config.enabled is False
    assert config.dsn is None
    assert config.graph is None
    assert config.domain == "s2p"


def test_shadow_config_enabled_requires_dsn():
    with pytest.raises(S2PShadowConfigError, match="S2P_AGE_DSN"):
        S2PShadowConfig.from_env(
            {
                "S2P_SHADOW_AGE": "1",
                "S2P_AGE_GRAPH": "protocol_v2_test_shadow",
                "S2P_AGE_TEST_MODE": "1",
            }
        )


def test_shadow_config_enabled_requires_graph():
    with pytest.raises(S2PShadowConfigError, match="S2P_AGE_GRAPH"):
        S2PShadowConfig.from_env(
            {
                "S2P_SHADOW_AGE": "1",
                "S2P_AGE_DSN": "postgresql://user:secret@127.0.0.1/db",
            }
        )


def test_shadow_config_rejects_soc_graph():
    with pytest.raises(S2PShadowConfigError, match="soc_graph"):
        S2PShadowConfig.from_env(
            {
                "S2P_SHADOW_AGE": "1",
                "S2P_AGE_DSN": "postgresql://user:secret@127.0.0.1/db",
                "S2P_AGE_GRAPH": "soc_graph",
            }
        )


def test_shadow_config_rejects_blank_graph():
    with pytest.raises(S2PShadowConfigError, match="S2P_AGE_GRAPH"):
        S2PShadowConfig.from_env(
            {
                "S2P_SHADOW_AGE": "1",
                "S2P_AGE_DSN": "postgresql://user:secret@127.0.0.1/db",
                "S2P_AGE_GRAPH": "  ",
            }
        )


def test_shadow_config_requires_test_mode_for_protocol_v2_test_graph():
    with pytest.raises(S2PShadowConfigError, match="S2P_AGE_TEST_MODE"):
        S2PShadowConfig.from_env(
            {
                "S2P_SHADOW_AGE": "1",
                "S2P_AGE_DSN": "postgresql://user:secret@127.0.0.1/db",
                "S2P_AGE_GRAPH": "protocol_v2_test_shadow",
            }
        )


def test_shadow_config_allows_protocol_v2_test_graph_with_test_mode():
    config = S2PShadowConfig.from_env(
        {
            "S2P_SHADOW_AGE": "1",
            "S2P_AGE_DSN": "postgresql://user:secret@127.0.0.1/db",
            "S2P_AGE_GRAPH": "protocol_v2_test_shadow",
            "S2P_AGE_TEST_MODE": "1",
        }
    )

    assert config.enabled is True
    assert config.graph == "protocol_v2_test_shadow"
    assert config.test_mode is True


def test_shadow_config_rejects_blank_domain():
    with pytest.raises(S2PShadowConfigError, match="S2P_AGE_DOMAIN"):
        S2PShadowConfig.from_env({"S2P_AGE_DOMAIN": "  "})


def test_shadow_config_rejects_non_s2p_domain():
    with pytest.raises(S2PShadowConfigError, match="s2p"):
        S2PShadowConfig.from_env({"S2P_AGE_DOMAIN": "trading"})


def test_shadow_config_redacts_dsn_in_safe_summary():
    config = S2PShadowConfig.from_env(
        {
            "S2P_SHADOW_AGE": "1",
            "S2P_AGE_DSN": "postgresql://postgres:postgres@127.0.0.1/db?password=abc",
            "S2P_AGE_GRAPH": "protocol_v2_test_shadow",
            "S2P_AGE_TEST_MODE": "1",
        }
    )

    summary = config.safe_summary()
    assert "postgres:postgres" not in summary["dsn"]
    assert "password=abc" not in summary["dsn"]
    assert "postgres:***" in summary["dsn"]
    assert "password=***" in summary["dsn"]


def test_shadow_diagnostics_records_supported_states_and_counts():
    diagnostics = S2PShadowDiagnostics(max_events=10, shadow_run_id="run-1")

    for status in sorted(SHADOW_STATUSES):
        event = diagnostics.record(operation="phase1", status=status)
        assert event.shadow_run_id == "run-1"
        assert event.operation_id

    counts = diagnostics.status_counts()
    for status in SHADOW_STATUSES:
        assert counts[status] == 1


def test_shadow_diagnostics_ring_buffer_enforces_max_size():
    diagnostics = S2PShadowDiagnostics(max_events=2, shadow_run_id="run-1")

    diagnostics.record(operation="first", status="disabled", operation_id="op-1")
    diagnostics.record(operation="second", status="skipped", operation_id="op-2")
    diagnostics.record(operation="third", status="succeeded", operation_id="op-3")

    assert [event.operation_id for event in diagnostics.events()] == ["op-2", "op-3"]


def test_shadow_diagnostics_redacts_error_message_secrets():
    diagnostics = S2PShadowDiagnostics(max_events=5, shadow_run_id="run-1")

    event = diagnostics.record(
        operation="connect",
        status="failed",
        error="postgresql://postgres:postgres@127.0.0.1/db?password=abc",
    )

    assert "postgres:postgres" not in event.error_message
    assert "password=abc" not in event.error_message
    assert diagnostics.last_error()["message"] == event.error_message


def test_shadow_module_does_not_construct_age_or_factory_when_disabled():
    from app.s2p_shadow import initialize_s2p_shadow_state  # noqa: WPS433

    called = False

    def _store_factory(config):
        nonlocal called
        called = True
        raise AssertionError("disabled shadow must not construct a store")

    state = initialize_s2p_shadow_state(env={}, store_factory=_store_factory)

    assert state.config.enabled is False
    assert state.store is None
    assert called is False


def test_shadow_disabled_env_does_not_change_default_s2p_score_route():
    from app.main import app, build_s2p_scorer  # noqa: WPS433
    from app.s2p_shadow import initialize_s2p_shadow_state  # noqa: WPS433

    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_shadow = initialize_s2p_shadow_state(env={})
    before = app.state.graph_store.count_decisions("s2p")

    response = TestClient(app).post("/api/s2p/score", json=VALID_SCORE_REQUEST)

    assert response.status_code == 200
    assert app.state.graph_store.count_decisions("s2p") == before + 1
    assert "AGE" not in type(app.state.graph_store).__name__
    assert not hasattr(app.state, "s2p_shadow_age_store")
