from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402


class FakeGraphStore:
    domain = "s2p"

    def __init__(self) -> None:
        self.verified = 2
        self.correct = 1
        self.categories = 1

    def count_verified(self, domain: str) -> int:
        assert domain == "s2p"
        return self.verified

    def count_correct(self, domain: str) -> int:
        assert domain == "s2p"
        return self.correct

    def count_verified_decisions(self, domain: str) -> int:
        assert domain == "s2p"
        return self.verified

    def get_decision(self, decision_id: str, domain: str | None = None):
        if domain is not None:
            assert domain == self.domain
        return None

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict | None = None,
        domain: str | None = None,
    ) -> None:
        raise AssertionError("conservation hook must not write outcomes")

    def get_archived_decisions(self, domain: str):
        assert domain == self.domain
        return []

    def count_categories_with_n(self, domain: str, n: int) -> int:
        assert domain == "s2p"
        assert n == 1
        return self.categories


class MissingCoverageGraphStore:
    domain = "s2p"

    def count_verified(self, domain: str) -> int:
        return 2

    def count_correct(self, domain: str) -> int:
        return 1

    def count_verified_decisions(self, domain: str) -> int:
        return 2

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
        raise AssertionError("conservation hook must not write outcomes")

    def get_archived_decisions(self, domain: str):
        return []


class RecordingLearningStore:
    def __init__(self, old_state: dict[str, object] | None = None) -> None:
        self.old_state = old_state
        self.updates: list[dict[str, object]] = []

    def get_conservation_state(self, domain: str) -> dict[str, object] | None:
        assert domain == "s2p"
        return self.old_state

    def update_conservation_state(self, **kwargs: object) -> str:
        self.updates.append(kwargs)
        return "state-id"


class FailingReadLearningStore(RecordingLearningStore):
    def get_conservation_state(self, domain: str) -> dict[str, object] | None:
        raise RuntimeError("read failed")


class FailingWriteLearningStore(RecordingLearningStore):
    def update_conservation_state(self, **kwargs: object) -> str:
        raise RuntimeError("write failed")


class RecordingLock:
    def __init__(self) -> None:
        self.active = False
        self.entries = 0

    def __enter__(self):
        self.active = True
        self.entries += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.active = False
        return False


class LockAssertingLearningStore(RecordingLearningStore):
    def __init__(self, lock: RecordingLock) -> None:
        super().__init__()
        self.lock = lock

    def get_conservation_state(self, domain: str) -> dict[str, object] | None:
        assert self.lock.active is True
        return None

    def update_conservation_state(self, **kwargs: object) -> str:
        assert self.lock.active is True
        return super().update_conservation_state(**kwargs)


def _fake_request(
    *,
    learning_store: Any | None = None,
    graph_store: Any | None = None,
) -> SimpleNamespace:
    graph_store = graph_store or FakeGraphStore()
    scorer = SimpleNamespace(
        graph_store=graph_store,
        _preset=SimpleNamespace(shape=SimpleNamespace(n_categories=5), penalty_ratio=1.0),
    )
    state = SimpleNamespace(scorer=scorer, graph_store=graph_store)
    if learning_store is not None:
        state.learning_store = learning_store
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _outcome_payload(decision_id: str, factor_vector: list[float]) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "outcome": "confirm",
        "analyst_action": "auto_approve",
        "analyst_id": "A001",
        "factor_vector": factor_vector,
        "category": "price_variance",
        "predicted_action": "auto_approve",
    }


def test_s2p_outcome_persists_l5_conservation_state() -> None:
    scorer = build_s2p_scorer(":memory:")
    app.state.scorer = scorer
    app.state.graph_store = scorer.graph_store
    app.state.s2p_reward_function = scorer._reward_fn
    client = TestClient(app)
    score = client.post(
        "/api/s2p/score",
        json={
            "event_id": "L5-S2P-001",
            "category": "price_variance",
            "amount": 1000.0,
            "supplier_id": "SUP-L5",
            "match_status": 0.9,
            "amount_variance_ratio": 0.2,
            "duplicate_score": 0.1,
            "supplier_exception_history": 0.1,
            "payment_terms_impact": 0.2,
            "commodity_index_correlation": 0.3,
            "tax_regulatory_compliance": 0.9,
        },
    ).json()

    response = client.post(
        "/api/s2p/outcome",
        json=_outcome_payload(score["decision_id"], score["factor_vector"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert "categories_with_data" not in payload
    assert "old_status" not in payload
    state = scorer.graph_store.get_conservation_state("s2p")
    assert state is not None
    assert state["domain"] == "s2p"
    assert state["caused_by_decision_id"] == score["decision_id"]
    assert state["old_status"] is None
    assert state["categories_total"] == 5
    assert state["categories_with_data"] == 1
    assert state["complacency_flag"] == "false"


def test_s2p_persistence_no_store_is_silent() -> None:
    request = _fake_request(graph_store=object())

    s2p_router._persist_l5_conservation_state(request, "S2P-NO-STORE")


def test_s2p_persistence_passes_old_status() -> None:
    store = RecordingLearningStore(old_state={"status": "RED"})
    request = _fake_request(learning_store=store)

    s2p_router._persist_l5_conservation_state(request, "S2P-OLD")

    assert len(store.updates) == 1
    update = store.updates[0]
    assert update["old_status"] == "RED"
    assert update["caused_by_decision_id"] == "S2P-OLD"
    assert update["categories_with_data"] == 1
    assert update["categories_total"] == 5


def test_s2p_persistence_get_failure_is_non_fatal() -> None:
    store = FailingReadLearningStore()
    request = _fake_request(learning_store=store)

    s2p_router._persist_l5_conservation_state(request, "S2P-GET-FAIL")

    assert store.updates == []


def test_s2p_persistence_update_failure_is_non_fatal() -> None:
    store = FailingWriteLearningStore()
    request = _fake_request(learning_store=store)

    s2p_router._persist_l5_conservation_state(request, "S2P-WRITE-FAIL")


def test_s2p_persistence_requires_real_category_coverage() -> None:
    store = RecordingLearningStore()
    request = _fake_request(learning_store=store, graph_store=MissingCoverageGraphStore())

    s2p_router._persist_l5_conservation_state(request, "S2P-NO-COVERAGE")

    assert store.updates == []


def test_s2p_persistence_read_write_remains_correct_without_router_lock() -> None:
    # Lock removed per s2p_handler_perf_design_v1.md §2 — mutation_lock_scope
    # serializes these writes. Keep the value assertion on the persistence path.
    store = RecordingLearningStore()
    request = _fake_request(learning_store=store)

    s2p_router._persist_l5_conservation_state(request, "S2P-LOCK")

    assert len(store.updates) == 1
