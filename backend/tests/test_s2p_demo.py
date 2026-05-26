"""
tests/test_s2p_demo.py - canonical S2P smoke scenarios.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.services.synthetic_invoices import SyntheticInvoiceGenerator
from demo.s2p_demo import build_demo_scorer, score_event


def _score_demo_invoices(n: int = 10):
    scorer = build_demo_scorer()
    invoices = SyntheticInvoiceGenerator(seed=11).generate(n)
    results = []
    for invoice in invoices:
        scored = score_event(scorer, invoice.factor_vector, invoice.category)
        results.append(
            {
                "id": invoice.invoice_id,
                "predicted": scored["action"],
                "expected": invoice.ground_truth_action,
                "correct": scored["action"] == invoice.ground_truth_action,
            }
        )
    return sum(1 for result in results if result["correct"]), results


def test_demo_runs_without_error():
    correct, results = _score_demo_invoices()
    assert isinstance(correct, int)
    assert len(results) == 10


def test_demo_beats_random_baseline():
    correct, _results = _score_demo_invoices()
    assert correct >= 3


def test_all_predicted_actions_are_s2p():
    _correct, results = _score_demo_invoices()
    valid = set(S2PDomainConfig.actions)
    for result in results:
        assert result["predicted"] in valid
        assert "suppress" not in result["predicted"]


def test_all_10_scenarios_scored():
    _correct, results = _score_demo_invoices()
    assert len(results) == 10
    assert [result["id"] for result in results] == [f"INV-{idx:05d}" for idx in range(1, 11)]
