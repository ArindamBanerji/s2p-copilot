"""
tests/test_s2p_domain_config.py - Versioned S2PDomainConfig tests.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig, S2PDomainConfigV2


def test_v2_categories_count_5():
    assert S2PDomainConfigV2.categories == [
        "price_variance",
        "quantity_mismatch",
        "duplicate_risk",
        "contract_gap",
        "format_compliance",
    ]
    assert S2PDomainConfigV2.n_categories == 5


def test_v2_actions_count_5():
    assert S2PDomainConfigV2.actions == [
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    ]
    assert S2PDomainConfigV2.n_actions == 5


def test_v2_factors_count_7():
    assert S2PDomainConfigV2.factors == [
        "match_status",
        "amount_variance_ratio",
        "duplicate_score",
        "supplier_exception_history",
        "payment_terms_impact",
        "commodity_index_correlation",
        "tax_regulatory_compliance",
    ]
    assert S2PDomainConfigV2.n_factors == 7


def test_v2_canonical_factors_count_4():
    assert S2PDomainConfigV2.canonical_factors == [
        "supplier_identity",
        "contract_linkage",
        "spend_category",
        "data_quality_score",
    ]
    assert len(S2PDomainConfigV2.canonical_factors) == 4


def test_v2_centroid_shape_5_5_7():
    centroids = S2PDomainConfigV2.get_profile_centroids()
    assert centroids.shape == (5, 5, 7)


def test_v2_centroids_bounded_0_1():
    centroids = S2PDomainConfigV2.get_profile_centroids()
    assert np.all(centroids >= 0.0)
    assert np.all(centroids <= 1.0)


def test_v2_domain_name_s2p():
    assert S2PDomainConfigV2.domain == "s2p"


def test_v2_calibration_profile_valid():
    profile = S2PDomainConfigV2.get_calibration_profile()
    assert profile.validate() == []
    assert profile.penalty_ratio == 5.0
    assert profile.temperature == 0.1
    assert profile.learning_rate == 0.05
    assert profile.extensions["eta"] == 0.05
    assert profile.extensions["eta_override"] == 0.01
    assert profile.extensions["q_window"] == 400
    assert profile.extensions["alpha_window"] == 50


def test_v2_auto_approve_high_match_status():
    centroids = S2PDomainConfigV2.get_profile_centroids()
    auto_approve_idx = S2PDomainConfigV2.get_action_index("auto_approve")
    match_status_idx = S2PDomainConfigV2.get_factor_index("match_status")
    assert centroids[0, auto_approve_idx, match_status_idx] == 0.95


def test_v2_no_soc_categories():
    assert "credential_access" not in S2PDomainConfigV2.categories


def test_v2_no_soc_actions():
    assert "escalate" not in S2PDomainConfigV2.actions
    assert "escalate_to_buyer" in S2PDomainConfigV2.actions


def test_v2_no_soc_factors():
    assert "travel_match" not in S2PDomainConfigV2.factors


def test_v2_canonical_not_in_scoring():
    assert set(S2PDomainConfigV2.factors).isdisjoint(
        S2PDomainConfigV2.canonical_factors
    )


def test_v2_scorer_accepts_shape():
    from gae import ProfileScorer

    scorer = ProfileScorer(
        mu=S2PDomainConfigV2.get_profile_centroids(),
        actions=S2PDomainConfigV2.actions,
        profile=S2PDomainConfigV2.get_calibration_profile(),
        categories=S2PDomainConfigV2.categories,
        eta_override=S2PDomainConfigV2.eta_override,
    )
    result = scorer.score(
        np.array([0.92, 0.08, 0.04, 0.05, 0.48, 0.76, 0.90], dtype=float),
        category_index=0,
    )
    assert result.action_name in S2PDomainConfigV2.actions
    assert 0 <= result.action_index < S2PDomainConfigV2.n_actions
    assert result.probabilities.shape == (S2PDomainConfigV2.n_actions,)
    assert abs(float(result.probabilities.sum()) - 1.0) < 1e-9


def test_legacy_config_unchanged():
    assert S2PDomainConfig.categories == [
        "maverick_spend",
        "supplier_risk",
        "contract_breach",
        "budget_overrun",
        "approval_bypass",
        "data_quality",
    ]
    assert S2PDomainConfig.actions == ["approve", "escalate", "reject", "review"]
    assert S2PDomainConfig.factors == [
        "spend_category_match",
        "supplier_risk_score",
        "contract_compliance",
        "spend_anomaly",
        "pattern_history",
        "vendor_trust",
    ]
    assert (
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    ) == (6, 4, 6)
    centroids = S2PDomainConfig.get_initial_centroids()
    assert len(centroids) == 6
    assert len(centroids["supplier_risk"]) == 4
    assert len(centroids["supplier_risk"]["approve"]) == 6
