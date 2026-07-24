from app.domains.s2p.evolution.rule_templates import AutoApproveThresholdRule
from app.domains.s2p.evolution.service import S2PEvolutionService
from app.domains.s2p.evolution.shadow_runner import S2PShadowRunner


def _variant():
    return {
        "variant_id": "auto_approve_threshold_sweep:price_variance:0.91",
        "template_name": "auto_approve_threshold_sweep",
        "category": "price_variance",
        "threshold": 0.91,
        "baseline_threshold": 0.79,
    }


def test_shadow_runner_empty_decisions_returns_no_results():
    result = S2PShadowRunner().run_batch(AutoApproveThresholdRule(), _variant(), [])

    assert result["sample_size"] == 0
    assert result["better"] is False
    assert result["win"] is False
    assert result["regression"] is False


def test_shadow_runner_returns_gate_compatible_batch_fields():
    decisions = [
        {
            "category": "price_variance",
            "recommended_action": "auto_approve",
            "ground_truth_action": "auto_approve",
            "confidence": 0.94,
        },
        {
            "category": "price_variance",
            "recommended_action": "auto_approve",
            "ground_truth_action": "hold_for_review",
            "confidence": 0.80,
        },
    ]

    result = S2PShadowRunner().run_batch(AutoApproveThresholdRule(), _variant(), decisions)

    for key in ("better", "win", "accuracy", "baseline_accuracy", "regression", "sample_size", "metric_name"):
        assert key in result
    assert result["better"] is True
    assert result["accuracy"] > result["baseline_accuracy"]


def test_shadow_runner_does_not_call_learn():
    class FakeScorer:
        def __init__(self):
            self.learn_calls = 0

        def learn(self, *_args, **_kwargs):
            self.learn_calls += 1
            raise AssertionError("learn must not be called")

    scorer = FakeScorer()

    service = S2PEvolutionService(scorer=scorer)
    service.run_shadow_batch("auto_approve_threshold_sweep", _variant()["variant_id"], [])

    assert scorer.learn_calls == 0


def test_shadow_runner_does_not_write_graph_store():
    class FakeGraphStore:
        def __init__(self):
            self.write_calls = 0

        def write_decision(
            self,
            domain: str,
            category: str,
            action: str,
            confidence: float,
            factors: dict,
            metadata: dict | None = None,
        ) -> str:
            self.write_calls += 1
            raise AssertionError("graph writes must not happen")

        def get_decision(self, decision_id: str, domain: str | None = None):
            return None

        def write_outcome(
            self,
            decision_id: str,
            actual_action: str,
            is_correct: bool,
            metadata: dict | None = None,
            domain: str | None = None,
        ) -> None:
            raise AssertionError("graph writes must not happen")

        def get_archived_decisions(self, domain: str):
            return []

    graph_store = FakeGraphStore()
    scorer = type("FakeScorer", (), {"graph_store": graph_store})()

    service = S2PEvolutionService(scorer=scorer)
    service.run_shadow_batch("auto_approve_threshold_sweep", _variant()["variant_id"], [])

    assert graph_store.write_calls == 0
