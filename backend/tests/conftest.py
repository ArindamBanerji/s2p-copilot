"""Test-process graph configuration for isolated S2P unit tests."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from copilot_sdk.testing.fixtures import age_available


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


@pytest.fixture(scope="session")
def s2p_age_test_env() -> S2PAgeTestEnvironment:
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
    with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
        conn.execute("LOAD 'age'")
        conn.execute('SET search_path = ag_catalog, "$user", public')
        conn.execute(f"SELECT create_graph('{active_graph_name}')")

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
    )
    try:
        yield environment
    finally:
        with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
            conn.execute("LOAD 'age'")
            conn.execute('SET search_path = ag_catalog, "$user", public')
            conn.execute(f"SELECT drop_graph('{active_graph_name}', true)")
