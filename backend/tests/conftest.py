"""Test-process graph configuration for isolated S2P unit tests."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

import pytest

from copilot_sdk.testing.fixtures import age_available
from copilot_sdk.graph.memory_store import InMemoryGraphStore


class S2PTestGraphStore(InMemoryGraphStore):
    """Complete unit-test GraphStore with AGE event filtering/order semantics."""

    def get_evolution_events(self, domain: str, **kwargs: Any) -> list[dict[str, Any]]:
        event_type = kwargs.get("event_type")
        events = super().get_evolution_events(
            domain, limit=kwargs.get("limit", 100), rule_name=kwargs.get("rule_name")
        )
        if isinstance(event_type, str):
            events = [event for event in events if event.get("event_type") == event_type]
        # Variant reconstruction consumes registration/status events in write
        # order so the last status update wins. AGE's adapter preserves this
        # order; keep the test store's contract identical.
        return list(events)


# Module-level app construction happens while pytest imports test modules. Keep
# that construction on an explicit local backend; tests that exercise active
# AGE override these values with their own scoped environment setup.
os.environ["GRAPH_BACKEND"] = "sqlite"
_ORIGINAL_GRAPH_DSN = os.environ.get("GRAPH_DSN", "")
os.environ.pop("GRAPH_DSN", None)
os.environ.pop("GRAPH_NAME", None)
os.environ["S2P_ACTIVE_GRAPH_BACKEND"] = "sqlite"
for _key in ("S2P_ACTIVE_AGE_DSN", "S2P_ACTIVE_AGE_GRAPH", "S2P_ACTIVE_AGE_DOMAIN"):
    os.environ.pop(_key, None)

_config_path = Path(tempfile.gettempdir()) / "s2p_pytest_graph_config.toml"
_config_path.write_text(
    """[defaults]
backend = \"sqlite\"
expected_backend = \"sqlite\"
dsn = \"\"
graph = \"soc_graph\"

[copilot.s2p]
domain = \"s2p\"
backend = \"sqlite\"
expected_backend = \"sqlite\"
prefix = \"S2P-\"
graph = \"soc_graph\"
""",
    encoding="utf-8",
)
os.environ["GRAPH_CONFIG_PATH"] = str(_config_path)
age_available.cache_clear()


@dataclass(frozen=True)
class S2PAgeTestEnvironment:
    active: dict[str, str]
    shadow: dict[str, str]
    shadow_namespace: str
    shadow_namespace_env: dict[str, str]


@pytest.fixture(scope="session")
def s2p_age_test_env() -> Generator[S2PAgeTestEnvironment, None, None]:
    """Provide one disposable AGE graph for S2P live integration tests."""
    dsn = os.environ.get("AGE_TEST_DSN", "").strip() or _ORIGINAL_GRAPH_DSN.strip()
    if not dsn:
        pytest.skip("AGE not available")

    import psycopg
    from uuid import uuid4

    try:
        conn = psycopg.connect(dsn, connect_timeout=3, autocommit=True)
        conn.execute("LOAD 'age'")
        conn.close()
    except Exception:
        pytest.skip("AGE not reachable")

    active_graph_name = f"protocol_v2_test_s2p_active_{uuid4().hex[:12]}"
    shadow_graph_name = f"protocol_v2_test_s2p_shadow_{uuid4().hex[:12]}"
    with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
        conn.execute("LOAD 'age'")
        conn.execute('SET search_path = ag_catalog, "$user", public')
        conn.execute(f"SELECT create_graph('{active_graph_name}')")
        conn.execute(f"SELECT create_graph('{shadow_graph_name}')")

    environment = S2PAgeTestEnvironment(
        active={
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": dsn,
            "S2P_ACTIVE_AGE_GRAPH": active_graph_name,
            "S2P_ACTIVE_AGE_DOMAIN": "s2p",
            "S2P_ACTIVE_AGE_TEST_MODE": "1",
        },
        shadow={
            "S2P_SHADOW_AGE": "1",
            "S2P_AGE_DSN": dsn,
            "S2P_AGE_GRAPH": active_graph_name,
            "S2P_AGE_TEST_MODE": "1",
        },
        shadow_namespace=shadow_graph_name,
        shadow_namespace_env={
            "S2P_SHADOW_AGE": "1",
            "S2P_SHADOW_AGE_DSN": dsn,
            "S2P_SHADOW_AGE_GRAPH": shadow_graph_name,
            "S2P_SHADOW_AGE_DOMAIN": "s2p",
            "S2P_SHADOW_AGE_TEST_MODE": "1",
        },
    )
    try:
        yield environment
    finally:
        with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
            conn.execute("LOAD 'age'")
            conn.execute('SET search_path = ag_catalog, "$user", public')
            conn.execute(f"SELECT drop_graph('{active_graph_name}', true)")
            conn.execute(f"SELECT drop_graph('{shadow_graph_name}', true)")


@pytest.fixture(autouse=True)
def isolated_age_compatible_evolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give unit tests an explicit complete GraphStore, never a SQLite fallback."""
    from app.services import s2p_evolver

    store = S2PTestGraphStore(domain="s2p")
    s2p_evolver.set_graph_store(store)

    def reset_store(graph_variant_store: object) -> None:
        graph_store = getattr(graph_variant_store, "graph_store", None)
        if not isinstance(graph_store, InMemoryGraphStore):
            raise AssertionError("S2P test evolver must use the isolated GraphStore")
        graph_store.reset()

    from copilot_sdk.evolution.graph_store import GraphVariantStore

    monkeypatch.setattr(GraphVariantStore, "reset", reset_store)
