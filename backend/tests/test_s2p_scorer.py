"""S2P CompoundingScorer wiring tests."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.main import build_s2p_scorer


def _factor_dict(value: float = 0.5) -> dict[str, float]:
    return {name: value for name in S2PDomainConfig.factors}


def test_legacy_s2p_scorer_module_removed():
    assert not Path("app/domains/s2p/scorer.py").exists()


def test_build_s2p_scorer_returns_compounding_scorer():
    from copilot_sdk.scoring import CompoundingScorer

    scorer = build_s2p_scorer(":memory:")
    assert isinstance(scorer, CompoundingScorer)


def test_score_returns_required_keys():
    scorer = build_s2p_scorer(":memory:")
    result = scorer.score(_factor_dict(), "price_variance")

    assert result.action in S2PDomainConfig.actions
    assert isinstance(result.decision_id, str)
    assert 0.0 <= result.confidence <= 1.0
    assert len(result.probabilities) == S2PDomainConfig.n_actions


def test_score_probabilities_sum_to_one():
    scorer = build_s2p_scorer(":memory:")
    result = scorer.score(_factor_dict(), "contract_gap")

    assert abs(sum(result.probabilities) - 1.0) < 0.01


def test_tensor_shape_remains_5_5_7():
    assert (
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    ) == (5, 5, S2PDomainConfig.n_factors)
