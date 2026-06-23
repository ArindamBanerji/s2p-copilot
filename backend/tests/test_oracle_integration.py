from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "copilot-sdk"))

from copilot_sdk.substantiation import Oracle

from app.oracle.buyer_oracle import BuyerOracle
from app.oracle.holdout import ConditionalHoldout
from app.oracle.pipeline_test import _sample_outcomes, run_all_experiments


def test_buyer_oracle_satisfies_sdk_protocol():
    oracle = BuyerOracle()

    assert isinstance(oracle, Oracle), "BuyerOracle must satisfy SDK Oracle protocol"
    assert oracle.known_effect == pytest.approx(0.08)
    assert oracle.known_accuracy_effect == pytest.approx(0.05)


def test_oracle_run_all_5_experiments():
    results = run_all_experiments()

    assert set(results) == {"exp1", "exp2", "exp3", "exp4", "exp5"}


def test_oracle_all_experiments_pass():
    results = run_all_experiments()

    assert all(result["pass"] is True for result in results.values())


def test_conditional_holdout_shape_differs_from_unconditional():
    holdout = ConditionalHoldout(holdout_pct=15, seed=404)

    no_enrichment = [
        holdout.suppressed(f"SUP-{index:04d}", has_enrichment=False)
        for index in range(1000)
    ]
    with_enrichment = [
        holdout.suppressed(f"SUP-{index:04d}", has_enrichment=True)
        for index in range(1000)
    ]

    assert not any(no_enrichment)
    assert any(with_enrichment)


def test_oracle_outcomes_contain_correct_field():
    outcomes = _sample_outcomes(BuyerOracle(seed=7), shown=True, n=100)

    assert all("correct" in outcome for outcome in outcomes)
    assert not all(outcome["correct"] is True for outcome in outcomes)


def test_holdout_sha256_deterministic():
    holdout = ConditionalHoldout(holdout_pct=15, seed=404)

    values = [
        holdout.suppressed("SUP-DETERMINISTIC", has_enrichment=True)
        for _ in range(1000)
    ]

    assert len(set(values)) == 1
