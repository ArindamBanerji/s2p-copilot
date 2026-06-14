from copy import deepcopy

from app.domains.s2p.config import S2PDomainConfig
from app.services.s2p_trust_explanations import (
    LEARNING_MESSAGE,
    format_trust_explanation,
    normalize_centroid,
    normalize_factor_values,
    normalize_weights,
)


FACTORS = list(S2PDomainConfig.factors)


def _format(**overrides):
    params = {
        "category": "price_variance",
        "recommended_action": "hold_for_review",
        "confidence": 0.82,
        "factor_values": {
            "match_status": 0.6,
            "amount_variance_ratio": 0.9,
            "duplicate_score": 0.1,
            "supplier_exception_history": 0.2,
            "payment_terms_impact": 0.7,
            "commodity_index_correlation": 0.95,
            "tax_regulatory_compliance": 0.4,
        },
        "factor_names": FACTORS,
        "dk_weights": {
            "match_status": 0.2,
            "amount_variance_ratio": 0.9,
            "duplicate_score": 0.1,
            "supplier_exception_history": 0.5,
            "payment_terms_impact": 0.4,
            "commodity_index_correlation": 0.8,
            "tax_regulatory_compliance": 0.3,
        },
        "centroid": {name: 0.5 for name in FACTORS},
        "category_index": 0,
        "phase": "variance_learning",
        "verified_count": 250,
    }
    params.update(overrides)
    return format_trust_explanation(**params)


def test_format_with_dk_weights_sorted_by_contribution():
    explanation = _format()

    assert explanation.trust_available is True
    contributions = [factor.contribution for factor in explanation.factors]
    assert contributions == sorted(contributions, reverse=True)
    assert explanation.factors[0].name == "amount_variance_ratio"


def test_format_without_dk_weights_pre_transition_learning():
    explanation = _format(dk_weights=None, phase=None, verified_count=None)

    assert explanation.trust_available is False
    assert explanation.learning_message == LEARNING_MESSAGE
    assert all(factor.dk_weight is None for factor in explanation.factors)
    assert all(factor.interpretation == "learning (pre-transition)" for factor in explanation.factors)


def test_pre_transition_uses_neutral_centroid_even_if_centroid_supplied():
    explanation = _format(
        dk_weights=None,
        factor_values={"match_status": 0.9},
        centroid={"match_status": 0.9},
    )

    factor = next(item for item in explanation.factors if item.name == "match_status")
    assert factor.centroid_mean == 0.5
    assert factor.contribution == 0.4


def test_interpretation_trusted():
    explanation = _format(dk_weights={"amount_variance_ratio": 0.71})

    factor = next(item for item in explanation.factors if item.name == "amount_variance_ratio")
    assert factor.interpretation == "trusted factor"


def test_interpretation_noisy():
    explanation = _format(dk_weights={"amount_variance_ratio": 0.29})

    factor = next(item for item in explanation.factors if item.name == "amount_variance_ratio")
    assert factor.interpretation == "noisy factor"


def test_interpretation_moderate_boundaries():
    low = _format(dk_weights={"amount_variance_ratio": 0.3})
    high = _format(dk_weights={"amount_variance_ratio": 0.7})

    assert next(item for item in low.factors if item.name == "amount_variance_ratio").interpretation == "moderate reliability"
    assert next(item for item in high.factors if item.name == "amount_variance_ratio").interpretation == "moderate reliability"


def test_summary_generation_uses_top_two_factor_names():
    explanation = _format()

    top_two = [factor.name for factor in explanation.factors[:2]]
    assert top_two[0] in explanation.summary
    assert top_two[1] in explanation.summary


def test_contribution_sorting_descending():
    explanation = _format()

    for left, right in zip(explanation.factors, explanation.factors[1:]):
        assert left.contribution >= right.contribution


def test_empty_factors_safe():
    explanation = format_trust_explanation(
        category="price_variance",
        recommended_action="hold_for_review",
        confidence=0.4,
        factor_values={},
        factor_names=[],
        dk_weights=None,
    )

    assert explanation.factors == []
    assert explanation.summary


def test_missing_dk_weight_safe():
    explanation = _format(dk_weights={"amount_variance_ratio": 0.9})

    missing = next(item for item in explanation.factors if item.name == "match_status")
    assert missing.dk_weight is None
    assert missing.interpretation == "learning (weight unavailable)"


def test_dk_dict_and_list_inputs_equivalent():
    dict_weights = normalize_weights({"match_status": 0.4}, FACTORS)
    list_weights = normalize_weights([0.4], FACTORS)

    assert dict_weights["match_status"] == list_weights["match_status"]


def test_nested_dk_weights_use_category_index():
    weights = normalize_weights([[0.1] * len(FACTORS), [0.8] * len(FACTORS)], FACTORS, category_index=1)

    assert weights["match_status"] == 0.8


def test_nested_dk_weights_without_category_index_safe_learning():
    assert normalize_weights([[0.1] * len(FACTORS)], FACTORS) is None


def test_centroid_dict_and_list_inputs_equivalent():
    dict_centroid = normalize_centroid({"match_status": 0.6}, FACTORS)
    list_centroid = normalize_centroid([0.6], FACTORS)

    assert dict_centroid["match_status"] == list_centroid["match_status"]


def test_missing_centroid_uses_neutral():
    centroid = normalize_centroid(None, FACTORS)

    assert set(centroid.values()) == {0.5}


def test_non_numeric_factor_safe():
    values = normalize_factor_values({"match_status": "bad"}, FACTORS)
    explanation = _format(factor_values=values)

    assert next(item for item in explanation.factors if item.name == "match_status").value is None


def test_inputs_not_mutated():
    values = {"match_status": 0.6}
    weights = {"match_status": 0.4}
    centroid = {"match_status": 0.5}
    before = deepcopy((values, weights, centroid))

    _format(factor_values=values, dk_weights=weights, centroid=centroid)

    assert (values, weights, centroid) == before


def test_uses_canonical_s2p_factor_names():
    explanation = _format(factor_names=[*FACTORS, "non_canonical"])

    assert {factor.name for factor in explanation.factors} == set(FACTORS)


def test_pre_transition_summary_has_no_trusted_or_noisy_labels():
    explanation = _format(dk_weights=None)
    payload = explanation.to_dict()

    assert "trusted factor" not in str(payload)
    assert "noisy factor" not in str(payload)
