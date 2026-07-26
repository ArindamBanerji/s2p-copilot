"""Test-process graph configuration for isolated S2P unit tests."""

import os
import tempfile
from pathlib import Path


# Module-level app construction happens while pytest imports test modules. Keep
# that construction on an explicit local backend; tests that exercise active
# AGE override these values with their own scoped environment setup.
os.environ["GRAPH_BACKEND"] = "sqlite"
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
