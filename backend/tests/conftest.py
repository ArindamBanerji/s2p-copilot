"""Test-process graph configuration for isolated S2P unit tests."""

import os
import json
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generator

import pytest

from copilot_sdk.testing.fixtures import age_available
from copilot_sdk.graph.memory_store import InMemoryGraphStore


class S2PTestGraphStore(InMemoryGraphStore):
    """Complete unit-test GraphStore with AGE event filtering/order semantics."""

    def __init__(self, domain: str = "s2p") -> None:
        super().__init__(domain=domain, decision_id_prefix="S2P-")

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


@pytest.fixture
def memory_store() -> InMemoryGraphStore:
    return InMemoryGraphStore(domain="s2p")


@pytest.fixture
def age_store() -> Generator[Any, None, None]:
    dsn = os.environ.get("GRAPH_DSN", "").strip()
    if not dsn:
        pytest.skip("GRAPH_DSN not set")
    from ci_platform.graph.age_client import AGEClient
    from ci_platform.graph.age_graph_store import AGEGraphStore

    graph_name = f"test_{uuid.uuid4().hex[:8]}"
    client = AGEClient(dsn=dsn, graph_name=graph_name)
    import asyncio

    asyncio.run(client.ensure_graph())
    asyncio.run(client.close())
    store = AGEGraphStore(dsn=dsn, graph_name=graph_name)
    try:
        yield store
    finally:
        store.close()
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
            conn.execute("LOAD 'age'")
            conn.execute('SET search_path = ag_catalog, "$user", public')
            conn.execute("SELECT drop_graph(%s, true)", (graph_name,))


# Module-level app construction happens while pytest imports test modules. Keep
# that construction on an explicit local backend; tests that exercise active
# AGE override these values with their own scoped environment setup.
os.environ["S2P_PROFILE"] = "test"
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


from app import s2p_graph_status as _s2p_graph_status
from app.s2p_graph_status import create_s2p_active_graph_store as _original_active_factory


def _isolated_active_graph_store(config: Any, *, store_factory: Any = None) -> Any:
    """Use an isolated store for local setup while preserving explicit AGE tests."""
    if config.requested_backend == "age":
        return _original_active_factory(config, store_factory=store_factory)
    return S2PTestGraphStore(domain="s2p")


from app import main as _s2p_main
from app.main import build_s2p_scorer as _original_build_factory


def _isolated_build_s2p_scorer(
    db_path: str | None = None,
    graph_store: Any = None,
    *,
    profile: str | None = None,
) -> Any:
    """Keep only DSN-less teardown construction on the complete memory store."""
    if graph_store is None and not os.environ.get("GRAPH_CONFIG_PATH"):
        graph_store = S2PTestGraphStore(domain="s2p")
    return _original_build_factory(db_path, graph_store=graph_store, profile=profile)




@pytest.fixture(autouse=True)
def isolated_factory_overrides(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Scope test-only factory injection to one test and restore it afterward."""
    monkeypatch.setattr(
        _s2p_graph_status,
        "create_s2p_active_graph_store",
        _isolated_active_graph_store,
    )
    monkeypatch.setattr(_s2p_main, "build_s2p_scorer", _isolated_build_s2p_scorer)
    # A few legacy tests imported the callable directly during collection.
    # Inject the same scoped factory into those aliases so their teardown
    # remains isolated when a test temporarily removes graph configuration.
    for module in list(sys.modules.values()):
        if module is None:
            continue
        if getattr(module, "build_s2p_scorer", None) is _original_build_factory:
            monkeypatch.setattr(module, "build_s2p_scorer", _isolated_build_s2p_scorer)
        if getattr(module, "create_s2p_active_graph_store", None) is _original_active_factory:
            monkeypatch.setattr(
                module,
                "create_s2p_active_graph_store",
                _isolated_active_graph_store,
            )
    yield


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
def isolated_age_compatible_evolver(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Give unit tests an explicit complete GraphStore, never a SQLite fallback."""
    from app.services import s2p_evolver

    previous = getattr(s2p_evolver, "_s2p_evolver", None)
    store = S2PTestGraphStore(domain="s2p")
    s2p_evolver.set_graph_store(store)
    try:
        yield
    finally:
        monkeypatch.setattr(s2p_evolver, "_s2p_evolver", previous)


@pytest.fixture(autouse=True)
def isolated_app_graph_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Start each test with an isolated complete GraphStore on app.state."""
    from app.graph.s2p_graph_reader import S2PGraphReader
    from app.routers.s2p_evidence import clear_evidence_context_cache

    store = S2PTestGraphStore(domain="s2p")
    scorer = _original_build_factory(graph_store=store)
    monkeypatch.setattr(_s2p_main.app.state, "scorer", scorer, raising=False)
    monkeypatch.setattr(_s2p_main.app.state, "graph_store", store, raising=False)
    monkeypatch.setattr(
        _s2p_main.app.state,
        "s2p_graph_reader",
        S2PGraphReader(store=store),
        raising=False,
    )
    monkeypatch.setattr(
        _s2p_main.app.state,
        "s2p_reward_function",
        scorer._reward_fn,
        raising=False,
    )
    clear_evidence_context_cache()
    yield


@pytest.fixture
def seed_preview_graph() -> Callable[[Any], None]:
    """Populate a test-owned graph; preview handlers never read these files."""
    from app.domains.s2p.config import S2PDomainConfig
    from app.routers.s2p_preview import reset_preview_state

    def seed(store: Any) -> None:
        invoices = json.loads((Path(__file__).resolve().parents[2] / "data/synthetic_invoices.json").read_text())[:50]
        for index, invoice in enumerate(invoices):
            factors = {name: invoice["factors"].get(name, 0.5) for name in S2PDomainConfig.factors}
            store.write_decision("s2p", invoice["category"], "auto_approve", 0.8, factors, metadata={
                **invoice["metadata"], "decision_id": f"preview-pending-{index}",
                "invoice_id": invoice["invoice_id"], "supplier_id": invoice["supplier_id"],
                "supplier_name": invoice["supplier_name"], "amount": invoice["amount"],
                "po_number": invoice["po_number"], "created_at": 1790000000 + index,
                "factor_names": list(S2PDomainConfig.factors),
            })
        for index in range(10):
            for period, created_at in enumerate((1735689600, 1751328000)):
                decision_id = store.write_decision(
                    "s2p", "price_variance", "auto_approve", 0.8,
                    {name: 0.5 for name in S2PDomainConfig.factors},
                    metadata={"decision_id": f"preview-history-{index}-{period}",
                              "invoice_id": f"history-{index}-{period}",
                              "supplier_id": f"SUP-{index + 1:03}",
                              "supplier_name": invoices[index]["supplier_name"],
                              "amount": 100.0, "created_at": created_at},
                )
                store.write_outcome(decision_id, "auto_approve" if period == 0 else "hold_for_review",
                                    True, domain="s2p", metadata={"on_time": period == 0, "in_full": True})
        reset_preview_state()

    return seed


@pytest.fixture
def preview_graph(isolated_app_graph_state: None, seed_preview_graph: Callable[[Any], None]) -> Any:
    store = _s2p_main.app.state.graph_store
    seed_preview_graph(store)
    return store
