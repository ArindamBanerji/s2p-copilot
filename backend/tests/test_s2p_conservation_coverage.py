"""Regression tests for S2P's shared conservation coverage provider."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest
from fastapi import Request

from copilot_sdk.backend import conservation_utils
from copilot_sdk.graph.memory_store import InMemoryGraphStore

from app.domains.s2p.config import S2PDomainConfig
from app.routers import s2p


class _Store:
    def __init__(
        self,
        categories_with_data: int,
        *,
        verified_count: int = 10,
        correct_count: int = 8,
        total_decisions: int | None = None,
    ) -> None:
        self.categories_with_data = categories_with_data
        self.verified_count = verified_count
        self.correct_count = correct_count
        self.total_decisions = verified_count if total_decisions is None else total_decisions

    def count_verified(self, domain: str) -> int:
        return self.verified_count

    def count_correct(self, domain: str) -> int:
        return self.correct_count

    def count_verified_decisions(self, domain: str) -> int:
        return self.total_decisions

    def count_categories_with_n(self, domain: str, n: int = 1) -> int:
        assert domain == "s2p"
        assert n == 1
        return self.categories_with_data

    def get_verified_decisions(self, domain: str) -> list[dict[str, object]]:
        assert domain == "s2p"
        # Return newest first to prove the S2P provider canonicalizes backend order.
        return [
            {
                "decision_id": f"decision-{index:03d}",
                "created_at": float(index),
                "category": f"category-{index % max(self.categories_with_data, 1)}",
                "is_correct": index < self.correct_count,
            }
            for index in reversed(range(self.verified_count))
        ]


def test_s2p_conservation_provider_preserves_category_coverage() -> None:
    payload = s2p._read_conservation_counts(_Store(3), "s2p")

    assert payload["categories_total"] == 5
    assert payload["categories_with_data"] == 3
    assert payload["verified_count"] == 10


def test_s2p_conservation_provider_clamps_coverage_to_domain() -> None:
    payload = s2p._read_conservation_counts(_Store(99), "s2p")

    assert payload["categories_with_data"] == payload["categories_total"] == 5


def _request_for_store(store: _Store | InMemoryGraphStore) -> Request:
    return Request({
        "type": "http",
        "app": SimpleNamespace(state=SimpleNamespace(graph_store=store)),
    })


def _public_status_for_store(store: _Store) -> str:
    state = SimpleNamespace(graph_store=store)
    counts = s2p.cached_conservation_state_provider(state)
    payload = conservation_utils.compute_conservation_status_payload("s2p", counts)
    return str(payload["status"])


def test_internal_and_public_conservation_agree_for_zero_decisions() -> None:
    store = _Store(0, verified_count=0, correct_count=0, total_decisions=0)
    s2p._clear_score_conservation_status_cache()

    assert s2p._current_conservation_status(_request_for_store(store)) == _public_status_for_store(store)


def test_internal_and_public_conservation_agree_for_bootstrap_decisions() -> None:
    store = _Store(3, verified_count=7, correct_count=6, total_decisions=7)
    s2p._clear_score_conservation_status_cache()

    state = s2p.cached_conservation_state_provider(SimpleNamespace(graph_store=store))
    assert [row["created_at"] for row in state["verified_decisions"]] == list(
        map(float, range(7))
    )
    assert s2p._current_conservation_status(_request_for_store(store)) == _public_status_for_store(store)


def test_internal_and_public_conservation_agree_for_normal_volume() -> None:
    store = _Store(5, verified_count=60, correct_count=56, total_decisions=60)
    s2p._clear_score_conservation_status_cache()

    assert s2p._current_conservation_status(_request_for_store(store)) == _public_status_for_store(store)


def test_internal_conservation_passes_category_coverage_to_engine(
    memory_store: InMemoryGraphStore,
) -> None:
    categories = S2PDomainConfig.categories[:4]
    seeded = [
        (f"coverage-{index:03d}", 1_700_000_000.0 + index // 2,
         categories[index % len(categories)], index < 11)
        for index in range(12)
    ]
    # Reverse insertion and tied timestamps exercise both F-02 ordering keys.
    for decision_id, created_at, category, correct in reversed(seeded):
        memory_store.write_decision(
            domain="s2p", category=category, action=S2PDomainConfig.actions[0],
            confidence=0.9,
            factors={name: 0.5 for name in S2PDomainConfig.factors},
            metadata={"decision_id": decision_id, "created_at": created_at},
        )
        memory_store.write_outcome(
            decision_id, S2PDomainConfig.actions[0 if correct else 1], correct,
            domain="s2p", outcome="confirmed" if correct else "overridden",
        )
    s2p._clear_score_conservation_status_cache()

    state = s2p.cached_conservation_state_provider(SimpleNamespace(graph_store=memory_store))
    rows = state["verified_decisions"]
    assert [(row["created_at"], row["decision_id"]) for row in rows] == [
        (created_at, decision_id) for decision_id, created_at, _, _ in seeded
    ]
    expected_coverage = Counter(category for _, _, category, _ in seeded)
    observed_coverage = Counter(row["category"] for row in rows)
    assert {category: observed_coverage[category] for category in S2PDomainConfig.categories} == {
        category: expected_coverage[category] for category in S2PDomainConfig.categories
    }
    payload = conservation_utils.compute_conservation_status_payload("s2p", state)
    assert payload["verified_count"] == len(seeded)
    assert payload["correct_count"] == sum(correct for _, _, _, correct in seeded)
    assert payload["q"] == pytest.approx(payload["correct_count"] / len(seeded))
    assert payload["categories_with_data"] == len(expected_coverage)
    assert payload["categories_total"] == len(S2PDomainConfig.categories)
    assert payload["alpha"] == pytest.approx(len(expected_coverage) / len(S2PDomainConfig.categories))
    assert s2p._current_conservation_status(_request_for_store(memory_store)) == payload["status"] == "GREEN"


def test_cold_start_status_keeps_learning_allowed() -> None:
    assert s2p._is_learning_paused("COLD_START") is False


def test_bootstrap_status_keeps_learning_allowed() -> None:
    assert s2p._is_learning_paused("BOOTSTRAP") is False
    assert s2p._is_learning_paused({"status": "BOOTSTRAP"}) is False
