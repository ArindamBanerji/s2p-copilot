"""
tests/test_s2p_scorer.py — S2P scorer wiring tests.

Run from backend/:
    pytest tests/test_s2p_scorer.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.scorer import get_scorer, reset_scorer, score_event
from app.domains.s2p.config import S2PDomainConfig


def test_get_scorer_returns_profile_scorer():
    from gae import ProfileScorer
    reset_scorer()
    scorer = get_scorer()
    assert isinstance(scorer, ProfileScorer)


def test_scorer_is_singleton():
    reset_scorer()
    s1 = get_scorer()
    s2 = get_scorer()
    assert s1 is s2


def test_score_event_returns_required_keys():
    reset_scorer()
    factor_vector = [0.9, 0.8, 0.85, 0.9, 0.5, 0.8]  # low risk
    result = score_event(factor_vector, "supplier_risk")
    assert "action" in result
    assert "confidence" in result
    assert "probabilities" in result
    assert result["action"] in S2PDomainConfig.actions


def test_score_event_action_is_valid_s2p_action():
    reset_scorer()
    factor_vector = [0.5] * 6
    result = score_event(factor_vector, "maverick_spend")
    assert result["action"] in ["approve", "escalate", "reject", "review"]
    assert "suppress" not in result["action"]    # SOC action must never appear
    assert "investigate" not in result["action"]  # SOC action must never appear


def test_score_event_probabilities_sum_to_one():
    reset_scorer()
    factor_vector = [0.5] * 6
    result = score_event(factor_vector, "contract_breach")
    assert abs(sum(result["probabilities"]) - 1.0) < 0.01


def test_reset_scorer_clears_singleton():
    s1 = get_scorer()
    reset_scorer()
    s2 = get_scorer()
    assert s1 is not s2
