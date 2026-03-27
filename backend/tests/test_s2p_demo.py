"""
tests/test_s2p_demo.py — 10-scenario demo tests.

Run from backend/:
    pytest tests/test_s2p_demo.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.scorer import reset_scorer
from demo.s2p_demo import run_demo


def test_demo_runs_without_error():
    reset_scorer()
    correct, results = run_demo()
    assert isinstance(correct, int)
    assert len(results) == 10


def test_demo_beats_random_baseline():
    reset_scorer()
    correct, results = run_demo()
    # Random baseline = 2.5/10 (4 actions, uniform)
    assert correct >= 3


def test_all_predicted_actions_are_s2p():
    reset_scorer()
    correct, results = run_demo()
    valid = set(S2PDomainConfig.actions)
    for r in results:
        assert r["predicted"] in valid, f"Invalid action: {r['predicted']}"
        assert "suppress" not in r["predicted"]


def test_all_10_scenarios_scored():
    reset_scorer()
    correct, results = run_demo()
    assert len(results) == 10
    ids = [r["id"] for r in results]
    assert ids == list(range(1, 11))
