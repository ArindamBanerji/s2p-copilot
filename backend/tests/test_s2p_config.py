"""
tests/test_s2p_config.py — S2PDomainConfig unit tests.

Run from backend/:
    pytest tests/test_s2p_config.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig, S2P_ACTIONS, S2P_FACTORS, S2P_CATEGORIES


def test_tensor_shape_correct():
    config = S2PDomainConfig
    assert config.n_categories == 6
    assert config.n_actions == 4
    assert config.n_factors == 6
    assert config.n_categories * config.n_actions * config.n_factors == 144


def test_actions_are_s2p_not_soc():
    assert "approve" in S2P_ACTIONS
    assert "escalate" in S2P_ACTIONS
    assert "suppress" not in S2P_ACTIONS       # SOC action — must not appear
    assert "investigate" not in S2P_ACTIONS    # SOC action — must not appear


def test_factors_are_s2p_not_soc():
    assert "spend_category_match" in S2P_FACTORS
    assert "vendor_trust" in S2P_FACTORS
    assert "travel_match" not in S2P_FACTORS           # SOC factor
    assert "threat_intel_enrichment" not in S2P_FACTORS  # SOC factor


def test_get_initial_centroids_shape():
    centroids = S2PDomainConfig.get_initial_centroids()
    assert len(centroids) == 6  # categories
    for cat in S2P_CATEGORIES:
        assert cat in centroids
        assert len(centroids[cat]) == 4  # actions
        for act in S2P_ACTIONS:
            assert len(centroids[cat][act]) == 6  # factors
            assert all(v == 0.5 for v in centroids[cat][act])


def test_penalty_ratio_is_s2p_not_soc():
    assert S2PDomainConfig.penalty_ratio == 5.0
    assert S2PDomainConfig.penalty_ratio != 20.0  # SOC value
