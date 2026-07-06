"""
tests/test_s2p_config.py - canonical S2PDomainConfig unit tests.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig, S2P_ACTIONS, S2P_FACTORS, S2P_CATEGORIES


def test_s2p_domain_config_shape():
    config = S2PDomainConfig
    assert config.n_categories == 5
    assert config.n_actions == 5
    assert config.n_factors == 8
    assert config.n_categories * config.n_actions * config.n_factors == 200


def test_actions_are_canonical_s2p_not_soc_or_legacy():
    assert S2P_ACTIONS == [
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    ]
    assert "suppress" not in S2P_ACTIONS
    assert "investigate" not in S2P_ACTIONS
    assert "approve" not in S2P_ACTIONS


def test_factors_are_canonical_s2p_not_soc_or_legacy():
    assert S2P_FACTORS == [
        "match_status",
        "amount_variance_ratio",
        "duplicate_score",
        "supplier_exception_history",
        "payment_terms_impact",
        "commodity_index_correlation",
        "tax_regulatory_compliance",
        "environmental_risk",
    ]
    assert "travel_match" not in S2P_FACTORS
    assert "spend_category_match" not in S2P_FACTORS


def test_categories_are_canonical_s2p():
    assert S2P_CATEGORIES == [
        "price_variance",
        "quantity_mismatch",
        "duplicate_risk",
        "contract_gap",
        "format_compliance",
    ]


def test_get_initial_centroids_shape():
    centroids = S2PDomainConfig.get_initial_centroids()
    assert len(centroids) == 5
    for cat in S2P_CATEGORIES:
        assert cat in centroids
        assert len(centroids[cat]) == 5
        for act in S2P_ACTIONS:
            assert len(centroids[cat][act]) == S2PDomainConfig.n_factors


def test_profile_centroids_shape():
    assert S2PDomainConfig.get_profile_centroids().shape == (
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    )


def test_penalty_ratio_and_learning_gate():
    assert S2PDomainConfig.penalty_ratio == 5.0
    assert S2PDomainConfig.penalty_ratio != 20.0
