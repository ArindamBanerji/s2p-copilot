from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers.s2p import set_l5_dk_welford_tracker  # noqa: E402
from copilot_sdk.graph.memory_store import InMemoryGraphStore  # noqa: E402
from copilot_sdk.scoring.dk_persistence import DKWelfordTracker  # noqa: E402
from copilot_sdk.scoring.startup_restore import restore_l5_runtime_state  # noqa: E402


def _welford_state() -> dict[str, object]:
    return {
        "confirmed_mean": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        "confirmed_m2": [0.0] * 7,
        "overridden_mean": [0.0] * 7,
        "overridden_m2": [0.0] * 7,
        "all_mean": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        "all_m2": [0.0] * 7,
        "n_all": 2,
    }


def test_s2p_startup_loads_dk_welford_from_l5_or_records_deferred() -> None:
    store = InMemoryGraphStore(domain="s2p")
    scorer = build_s2p_scorer(graph_store=store)
    weights = [[1.0] * 7 for _ in range(5)]
    store.update_dk_weights(
        "s2p",
        weights,
        2,
        1.0,
        welford_state=_welford_state(),
        n_confirmed=2,
        n_overridden=0,
    )

    status = restore_l5_runtime_state(domain="s2p", scorer=scorer, learning_store=store)
    set_l5_dk_welford_tracker(status["welford_tracker"])

    assert status["dk_source"] == "l5"
    assert status["welford_source"] == "l5"
    assert scorer.get_dk_weights() == [[*row, 1.0] for row in weights]
    assert isinstance(status["welford_tracker"], DKWelfordTracker)


def test_s2p_startup_l5_unavailable_does_not_fail() -> None:
    scorer = build_s2p_scorer()

    status = restore_l5_runtime_state(domain="s2p", scorer=scorer, learning_store=None)

    assert status["dk_source"] == "missing"
    assert status["welford_source"] == "missing"
    assert status["centroid_source"] == "missing"
    assert status["conservation_source"] == "missing"


def test_s2p_startup_conservation_none_expected() -> None:
    store = InMemoryGraphStore(domain="s2p")
    scorer = build_s2p_scorer(graph_store=store)

    status = restore_l5_runtime_state(domain="s2p", scorer=scorer, learning_store=store)

    assert status["conservation_source"] == "missing"


def test_s2p_startup_status_indicates_source() -> None:
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        status = client.app.state.l5_startup_status

    assert status["dk_source"] in {"missing", "l5", "error", "deferred"}
    assert status["welford_source"] in {"missing", "l5", "error"}
    assert status["centroid_source"] in {"missing", "l5", "error", "deferred"}
    assert status["conservation_source"] in {"missing", "l5", "error"}
