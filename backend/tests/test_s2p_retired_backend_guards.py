"""Regression guards for retired S2P graph backends."""

import pytest

from copilot_sdk.graph.factory import create_graph_store


class TestRetiredBackendGuards:
    """Verify SQLite backend is permanently rejected."""

    def test_sqlite_backend_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="sqlite"):
            create_graph_store(backend="sqlite")

    def test_sqlite_backend_case_insensitive(self) -> None:
        with pytest.raises(ValueError, match="(?i)sqlite"):
            create_graph_store(backend="SQLite")

    def test_memory_backend_accepted(self) -> None:
        store = create_graph_store(
            backend="memory",
            domain="s2p",
            env={},
            profile="test",
        )
        assert store is not None
        store.close()

    def test_age_backend_accepted_or_skipped(self) -> None:
        try:
            store = create_graph_store(
                backend="age",
                domain="s2p",
                dsn="host=localhost port=5433 dbname=s2p user=s2p password=s2p",
                graph_name="protocol_v2_test_s2p_retired_backend_guards",
                env={},
                test_mode=True,
                profile="test",
            )
            assert store is not None
            store.close()
        except Exception as exc:
            message = str(exc)
            if (
                "GRAPH_DSN" in message
                or "connection" in message.lower()
                or "age" in message.lower()
            ):
                pytest.skip("AGE not available in this environment")
            raise

    def test_default_backend_is_not_sqlite(self) -> None:
        store = create_graph_store(domain="s2p", profile="test")
        assert store is not None
        store.close()

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError):
            create_graph_store(backend="postgres")
