"""
tests/test_s2p_domain_config.py - Canonical S2PDomainConfig tests.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig


def test_categories_count_5():
    assert S2PDomainConfig.categories == [
        "price_variance",
        "quantity_mismatch",
        "duplicate_risk",
        "contract_gap",
        "format_compliance",
    ]
    assert S2PDomainConfig.n_categories == 5


def test_actions_count_5():
    assert S2PDomainConfig.actions == [
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    ]
    assert S2PDomainConfig.n_actions == 5


def test_factors_count_7():
    assert S2PDomainConfig.factors == [
        "match_status",
        "amount_variance_ratio",
        "duplicate_score",
        "supplier_exception_history",
        "payment_terms_impact",
        "commodity_index_correlation",
        "tax_regulatory_compliance",
    ]
    assert S2PDomainConfig.n_factors == 7


def test_canonical_factors_count_4():
    assert S2PDomainConfig.canonical_factors == [
        "supplier_identity",
        "contract_linkage",
        "spend_category",
        "data_quality_score",
    ]
    assert len(S2PDomainConfig.canonical_factors) == 4


def test_centroid_shape_5_5_7():
    centroids = S2PDomainConfig.get_profile_centroids()
    assert centroids.shape == (5, 5, 7)


def test_centroids_bounded_0_1():
    centroids = S2PDomainConfig.get_profile_centroids()
    assert np.all(centroids >= 0.0)
    assert np.all(centroids <= 1.0)


def test_domain_name_s2p():
    assert S2PDomainConfig.domain == "s2p"


def test_calibration_profile_valid():
    profile = S2PDomainConfig.get_calibration_profile()
    assert profile.validate() == []
    assert profile.penalty_ratio == 5.0
    assert profile.temperature == 0.1
    assert profile.learning_rate == 0.05
    assert profile.extensions["eta"] == 0.05
    assert profile.extensions["eta_override"] == 0.01
    assert profile.extensions["q_window"] == 400
    assert profile.extensions["alpha_window"] == 50


def test_auto_approve_high_match_status():
    centroids = S2PDomainConfig.get_profile_centroids()
    auto_approve_idx = S2PDomainConfig.get_action_index("auto_approve")
    match_status_idx = S2PDomainConfig.get_factor_index("match_status")
    assert centroids[0, auto_approve_idx, match_status_idx] == 0.95


def test_no_soc_or_legacy_domain_terms():
    assert "credential_access" not in S2PDomainConfig.categories
    assert "supplier_risk" not in S2PDomainConfig.categories
    assert "escalate" not in S2PDomainConfig.actions
    assert "escalate_to_buyer" in S2PDomainConfig.actions
    assert "travel_match" not in S2PDomainConfig.factors
    assert "spend_category_match" not in S2PDomainConfig.factors


def test_canonical_factors_not_in_scoring_factors():
    assert set(S2PDomainConfig.factors).isdisjoint(S2PDomainConfig.canonical_factors)


def test_scorer_accepts_shape():
    from gae import ProfileScorer

    scorer = ProfileScorer(
        mu=S2PDomainConfig.get_profile_centroids(),
        actions=S2PDomainConfig.actions,
        profile=S2PDomainConfig.get_calibration_profile(),
        categories=S2PDomainConfig.categories,
        eta_override=S2PDomainConfig.eta_override,
    )
    result = scorer.score(
        np.array([0.92, 0.08, 0.04, 0.05, 0.48, 0.76, 0.90], dtype=float),
        category_index=0,
    )
    assert result.action_name in S2PDomainConfig.actions
    assert 0 <= result.action_index < S2PDomainConfig.n_actions
    assert result.probabilities.shape == (S2PDomainConfig.n_actions,)
    assert abs(float(result.probabilities.sum()) - 1.0) < 1e-9
