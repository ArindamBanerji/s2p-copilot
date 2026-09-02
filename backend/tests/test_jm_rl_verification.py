"""S2P-specific verification of the shared JM and RL contracts.

These tests use the production InMemoryGraphStore implementation as an
isolated GraphStore.  The same domain-scoped methods are used by the AGE
adapter, so the assertions exercise the protocol boundary without requiring a
shared database in every test process.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.reward import S2PGradedRewardFunction
from app.main import build_s2p_scorer
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.rl import CreditAssigner, DomainRewardFunction, ExplorationPolicy, RewardComputer


DOMAIN = "s2p"
FACTORS = {name: 0.5 for name in S2PDomainConfig.factors}


@pytest.fixture
def store() -> InMemoryGraphStore:
    return InMemoryGraphStore(domain=DOMAIN, decision_id_prefix="S2P-")


@pytest.fixture
def scorer(store: InMemoryGraphStore) -> Any:
    return build_s2p_scorer(graph_store=store, profile="test")


def _decision(store: InMemoryGraphStore, *, domain: str = DOMAIN) -> str:
    return store.write_decision(
        domain=domain,
        category=S2PDomainConfig.categories[0],
        action=S2PDomainConfig.actions[0],
        confidence=0.9,
        factors=FACTORS,
        metadata={"factor_vector": list(FACTORS.values())},
    )


class TestS2PJM:
    def test_graph_store_satisfies_protocol(self, store: InMemoryGraphStore) -> None:
        assert isinstance(store, GraphStore)

    def test_decision_round_trip_is_domain_scoped(self, store: InMemoryGraphStore) -> None:
        decision_id = _decision(store)
        saved = store.get_decision(decision_id, DOMAIN)
        assert saved is not None
        assert saved["decision_id"] == decision_id
        assert saved["domain"] == DOMAIN

    def test_other_domain_cannot_read_s2p_decision(self, store: InMemoryGraphStore) -> None:
        decision_id = _decision(store)
        assert store.get_decision(decision_id, "trading") is None

    def test_domain_query_excludes_other_copilot_decisions(self, store: InMemoryGraphStore) -> None:
        _decision(store)
        store.write_decision("trading", "market", "buy", 0.8, {"signal": 0.5})
        assert len(store.get_all_decisions(DOMAIN)) == 1
        assert len(store.get_all_decisions("trading")) == 1

    def test_every_decision_write_stamps_s2p_domain(self, store: InMemoryGraphStore) -> None:
        decision_id = _decision(store)
        assert store.get_decision(decision_id, DOMAIN)["domain"] == DOMAIN

    def test_evolution_state_round_trip(self, store: InMemoryGraphStore) -> None:
        state = {"variant_id": "s2p-v1", "status": "active", "score": 0.73}
        store.save_evolution_state(DOMAIN, "s2p-v1", state)
        assert store.get_evolution_state(DOMAIN, "s2p-v1") == state
        assert store.get_evolution_state("trading", "s2p-v1") is None

    def test_posterior_state_round_trip(self, store: InMemoryGraphStore) -> None:
        state = {"alpha": [1.0] * 5, "beta": [1.0] * 5}
        store.save_posterior(DOMAIN, "s2p-posteriors", state)
        assert store.get_posterior(DOMAIN, "s2p-posteriors") == state

    def test_promotion_state_round_trip(self, store: InMemoryGraphStore) -> None:
        state = {"status": "candidate", "verified_count": 12}
        store.save_promotion(DOMAIN, "rule-1", state)
        assert store.get_promotion(DOMAIN, "rule-1") == state
        assert store.get_promotion("trading", "rule-1") is None

    def test_centroid_checkpoint_round_trip(self, store: InMemoryGraphStore) -> None:
        centroids = np.zeros((5, 5, 8), dtype=float)
        store.save_centroids(DOMAIN, S2PDomainConfig.categories[0], centroids, {"iks": 0.4})
        restored = np.asarray(store.load_latest_centroids(DOMAIN))
        assert restored.shape == (5, 5, 8)
        assert np.array_equal(restored, centroids)

    def test_centroid_checkpoint_is_domain_scoped(self, store: InMemoryGraphStore) -> None:
        store.save_centroids(DOMAIN, "price_variance", np.ones((5, 5, 8)))
        assert store.load_latest_centroids("trading") is None

    def test_compounding_ledger_is_graph_state(self, store: InMemoryGraphStore) -> None:
        payload = {"decision_id": "S2P-1", "reward": 0.8, "domain": DOMAIN}
        store.save_ledger(DOMAIN, "reward-1", payload)
        assert store.get_ledger(DOMAIN, "reward-1") == payload
        assert store.list_ledgers("trading") == []

    def test_conservation_state_round_trip_and_formula(self, store: InMemoryGraphStore) -> None:
        verified, correct = 20, 18
        q = correct / verified
        alpha = 0.9
        theta_min = 0.75
        store.update_conservation_state(
            DOMAIN, "GREEN", alpha, q, verified, theta_min, alpha * q,
            S2PDomainConfig.n_categories, S2PDomainConfig.n_categories, 0.5, 0.1, "false",
        )
        state = store.get_conservation_state(DOMAIN)
        assert state is not None
        assert state["product"] == pytest.approx(alpha * q)
        assert state["product"] >= theta_min

    def test_s2p_tensor_shape_is_200(self) -> None:
        tensor = S2PDomainConfig.get_profile_centroids()
        assert tensor.shape == (5, 5, 8)
        assert tensor.size == 200

    def test_scorer_writes_domain_stamped_decision(self, scorer: Any, store: InMemoryGraphStore) -> None:
        result = scorer.score(FACTORS, S2PDomainConfig.categories[0])
        decision = store.get_decision(result.decision_id, DOMAIN)
        assert decision is not None
        assert decision["domain"] == DOMAIN

    def test_graph_failure_is_not_silently_substituted(self) -> None:
        from copilot_sdk.graph.sqlite_store import SQLiteGraphStore

        path = Path("s2p-jm-verification.sqlite3")
        sqlite_store = SQLiteGraphStore(path, domain=DOMAIN)
        try:
            with pytest.raises((TypeError, ValueError, RuntimeError)):
                build_s2p_scorer(graph_store=sqlite_store, profile="production")
        finally:
            sqlite_store.close()
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(f"{path}{suffix}")
                if candidate.exists():
                    candidate.unlink()

    def test_scoring_cycle_creates_no_sqlite_files(self, scorer: Any, tmp_path: Path) -> None:
        before = set(tmp_path.glob("*") )
        result = scorer.score(FACTORS, S2PDomainConfig.categories[0])
        scorer.learn(result.decision_id, result.action, context={"preseed": True}, persist_artifacts=False)
        assert set(tmp_path.glob("*")) == before


class TestS2PRL:
    def test_graded_reward_satisfies_domain_protocol(self) -> None:
        assert isinstance(S2PGradedRewardFunction(), DomainRewardFunction)

    def test_reward_range_is_unit_interval(self) -> None:
        assert S2PGradedRewardFunction().reward_range() == (0.0, 1.0)

    def test_perfect_outcome_has_full_reward(self) -> None:
        fn = S2PGradedRewardFunction()
        assert fn.compute_reward({"action": "approve"}, {"action": "approve", "recovery_pct": 100}) == pytest.approx(1.0)

    def test_worst_outcome_has_zero_reward(self) -> None:
        fn = S2PGradedRewardFunction()
        assert fn.compute_reward({"action": "approve"}, {"action": "hold", "amount": 100, "at_risk": 100}) == pytest.approx(0.0)

    def test_partial_accuracy_is_graded(self) -> None:
        fn = S2PGradedRewardFunction()
        value = fn.compute_reward({"action": "approve"}, {"action": "approve", "exception_accuracy": 0.4, "savings_ratio": 1.0})
        assert 0.0 < value < 1.0

    def test_reward_computer_preserves_domain_and_bounds(self) -> None:
        computer = RewardComputer(S2PGradedRewardFunction(), domain=DOMAIN)
        result = computer.compute("approve", "approve", {"recovery_pct": 50})
        assert result.domain == DOMAIN
        assert result.reward == pytest.approx(0.5)
        assert 0.0 <= result.reward <= 1.0

    def test_reward_persists_in_graph_ledger(self, store: InMemoryGraphStore) -> None:
        computer = RewardComputer(S2PGradedRewardFunction(), domain=DOMAIN)
        result = computer.compute("approve", "approve", {"recovery_pct": 80}, decision_id="S2P-1")
        entry_id = computer.persist(store, result)
        assert store.get_ledger(DOMAIN, entry_id)["reward"] == pytest.approx(0.8)

    def test_credit_assigner_discounts_delayed_outcomes(self) -> None:
        assignments = CreditAssigner(temporal_discount=0.9).assign_temporal(1.0, [("d0", 0), ("d2", 2)])
        assert assignments[0].credit == pytest.approx(1.0)
        assert assignments[1].credit == pytest.approx(0.81)

    def test_credit_assigner_distributes_factor_credit(self) -> None:
        credit = CreditAssigner(temporal_discount=1.0).assign(1.0, ["a", "b"], {"a": 3, "b": 1})
        assert credit["a"] == pytest.approx(0.75)
        assert credit["b"] == pytest.approx(0.25)

    def test_exploration_policy_respects_green_bound(self) -> None:
        policy = ExplorationPolicy(5, epsilon=0.125)
        decision = policy.select_action([1, 0, 0, 0, 0])
        assert 0.0 <= decision.epsilon <= 0.125

    def test_exploration_policy_disables_exploration_at_red(self) -> None:
        policy = ExplorationPolicy(5, epsilon=0.125)
        policy.set_conservation_status("RED")
        decision = policy.select_action([1, 0, 0, 0, 0])
        assert decision.epsilon == 0.0
        assert decision.explored is False
        assert decision.action == 0

    def test_scorer_learn_accepts_s2p_graded_reward(self, scorer: Any) -> None:
        result = scorer.score(FACTORS, S2PDomainConfig.categories[0])
        learned = scorer.learn(result.decision_id, result.action, context={"preseed": True}, persist_artifacts=False)
        assert learned.decision_id == result.decision_id
        assert learned.reward is not None
        assert 0.0 <= learned.reward <= 1.0

    def test_one_hundred_score_learn_cycles_preserve_bounded_iks(self, scorer: Any) -> None:
        for _ in range(100):
            result = scorer.score(FACTORS, S2PDomainConfig.categories[0])
            scorer.learn(result.decision_id, result.action, context={"preseed": True}, persist_artifacts=False)
        iks = float(scorer.trajectory().current_iks)
        assert np.isfinite(iks)
        assert iks >= 0.0
        assert scorer.get_conservation_state()["status"] in {"GREEN", "AMBER", "RED"}

    def test_reward_history_is_retrievable(self, store: InMemoryGraphStore) -> None:
        for index in range(3):
            store.save_ledger(DOMAIN, f"reward-{index}", {"reward": index / 2, "domain": DOMAIN})
        history = store.list_ledgers(DOMAIN)
        assert len(history) == 3
        assert {row["reward"] for row in history} == {0.0, 0.5, 1.0}

    def test_binary_full_reward_matches_soc_style(self) -> None:
        fn = S2PGradedRewardFunction()
        assert fn.compute_reward({"action": "approve"}, {"action": "approve", "exception_accuracy": 1.0}) == pytest.approx(1.0)

    def test_factor_count_mismatch_raises(self, scorer: Any) -> None:
        with pytest.raises((KeyError, ValueError, AssertionError)):
            S2PGradedRewardFunction().compute_reward(
                {"action": "approve", "factor_vector": [0.5] * 7},
                {"action": "approve"},
            )

    def test_reward_rejects_out_of_range_accuracy_by_clamping(self) -> None:
        fn = S2PGradedRewardFunction()
        assert fn.compute_reward({"action": "approve"}, {"action": "approve", "exception_accuracy": 2.0}) == pytest.approx(1.0)

    def test_reward_never_exceeds_unit_interval_on_mismatch(self) -> None:
        fn = S2PGradedRewardFunction()
        value = fn.compute_reward({"action": "approve"}, {"action": "hold", "amount": 1, "at_risk": 1000})
        assert 0.0 <= value <= 1.0

    def test_empty_credit_sequence_is_well_defined(self) -> None:
        assert CreditAssigner().assign(1.0, []) == {}

    def test_exploration_rejects_invalid_conservation_status(self) -> None:
        with pytest.raises(ValueError):
            ExplorationPolicy(5).set_conservation_status("UNKNOWN")

    def test_s2p_reward_computer_uses_s2p_domain(self) -> None:
        computer = RewardComputer(S2PGradedRewardFunction(), domain=DOMAIN)
        assert computer.compute("a", "a", {"recovery_pct": 100}).domain == DOMAIN


class TestS2PAuth:
    def test_auth_is_disabled_by_default(self) -> None:
        if os.environ.get("AUTH_ENABLED", "false").strip().lower() == "true":
            pytest.skip("AUTH_ENABLED is explicitly enabled for this process")
        from app.main import app

        assert not any(getattr(route, "path", "") == "/saml/status" for route in app.routes)

    def test_auth_mount_is_shared_sdk_route_when_enabled(self) -> None:
        from app.main import app

        enabled = os.environ.get("AUTH_ENABLED", "false").strip().lower() == "true"
        mounted = any(getattr(route, "path", "") == "/saml/status" for route in app.routes)
        assert mounted is enabled
