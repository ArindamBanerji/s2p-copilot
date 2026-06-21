from __future__ import annotations

from app.oracle.buyer_oracle import BuyerOracle
from app.oracle.holdout import ConditionalHoldout
from app.oracle.pipeline_test import (
    exp1_known_lift,
    exp2_zero_lift,
    exp3_floor_power,
    exp4_gate_rejects,
    exp5_conditional_coverage,
    run_all_experiments,
)


def _hold_rate(oracle: BuyerOracle, *, shown: bool, n: int = 5000) -> float:
    outcomes = [oracle.synthetic_outcome(shown=shown) for _ in range(n)]
    return sum(1 for row in outcomes if row["buyer_action"] == "hold_for_review") / len(outcomes)


def test_buyer_oracle_deterministic():
    left = BuyerOracle(seed=7)
    right = BuyerOracle(seed=7)

    assert [left.synthetic_outcome(shown=True) for _ in range(25)] == [
        right.synthetic_outcome(shown=True) for _ in range(25)
    ]


def test_buyer_oracle_treatment_higher_rate():
    control_rate = _hold_rate(BuyerOracle(seed=11), shown=False)
    treatment_rate = _hold_rate(BuyerOracle(seed=11), shown=True)

    assert treatment_rate > control_rate + 0.04


def test_buyer_oracle_correct_modeled():
    row = BuyerOracle(seed=3).synthetic_outcome(shown=True)

    assert isinstance(row["correct"], bool)
    assert row["quality_signal"] in {0.0, 1.0}


def test_buyer_oracle_all_fields():
    row = BuyerOracle(seed=4).synthetic_outcome(shown=False)

    assert set(row) == {"buyer_action", "was_override", "quality_signal", "correct"}
    assert row["buyer_action"] in {"auto_approve", "hold_for_review", "escalate"}
    assert isinstance(row["was_override"], bool)


def test_buyer_oracle_zero_lift():
    control_rate = _hold_rate(BuyerOracle(treatment_lift=0.0, accuracy_lift=0.0, seed=17), shown=False)
    treatment_rate = _hold_rate(BuyerOracle(treatment_lift=0.0, accuracy_lift=0.0, seed=17), shown=True)

    assert abs(treatment_rate - control_rate) < 0.025


def test_holdout_no_enrichment_never_suppressed():
    holdout = ConditionalHoldout(holdout_pct=100, seed=5)

    assert not holdout.suppressed("SUP-001", has_enrichment=False)


def test_holdout_enriched_some_suppressed():
    holdout = ConditionalHoldout(holdout_pct=15, seed=5)
    decisions = [
        holdout.suppressed(f"SUP-{index:04d}", has_enrichment=True)
        for index in range(500)
    ]

    assert any(decisions)
    assert not all(decisions)


def test_holdout_deterministic_per_supplier():
    holdout = ConditionalHoldout(holdout_pct=15, seed=5)

    assert holdout.suppressed("SUP-DET", has_enrichment=True) == holdout.suppressed(
        "SUP-DET",
        has_enrichment=True,
    )


def test_holdout_rate_approximately_correct():
    holdout = ConditionalHoldout(holdout_pct=15, seed=5)
    decisions = [
        holdout.suppressed(f"SUP-{index:05d}", has_enrichment=True)
        for index in range(10000)
    ]
    rate = sum(decisions) / len(decisions)

    assert 0.13 <= rate <= 0.17


def test_exp1_known_lift():
    result = exp1_known_lift()

    assert result["pass"] is True
    assert 0.055 <= result["measured"] <= 0.105


def test_exp2_zero_lift():
    result = exp2_zero_lift()

    assert result["pass"] is True
    assert abs(result["measured"]) <= 0.025


def test_exp3_floor_power():
    result = exp3_floor_power()

    assert result["pass"] is True
    assert result["n_per_arm"] == 588


def test_exp4_gate_rejects():
    result = exp4_gate_rejects()

    assert result["pass"] is True
    assert result["gate_rejected"] is True
    assert result["accuracy_delta"] < 0.0


def test_exp5_conditional_coverage():
    result = exp5_conditional_coverage()

    assert result["pass"] is True
    assert result["enriched_pct"] == 0.6
    assert 0.075 <= result["effective_holdout_pct"] <= 0.105


def test_run_all_experiments():
    results = run_all_experiments()

    assert set(results) == {"exp1", "exp2", "exp3", "exp4", "exp5"}
    assert all(result["pass"] is True for result in results.values())
