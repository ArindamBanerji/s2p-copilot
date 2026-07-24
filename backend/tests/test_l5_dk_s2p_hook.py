from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402
from copilot_sdk.scoring.dk_persistence import DKWelfordTracker  # noqa: E402


class RecordingDKLearningStore:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.updates: list[dict[str, object]] = []

    def update_dk_weights(self, **kwargs: object) -> None:
        if self.fail:
            raise RuntimeError("dk write failed")
        self.updates.append(kwargs)


class RecordingCentroidLearningStore(RecordingDKLearningStore):
    def __init__(self, fail: bool = False) -> None:
        super().__init__(fail=False)
        self.fail_centroid = fail
        self.centroid_updates: list[dict[str, object]] = []

    def update_centroid(self, **kwargs: object) -> None:
        if self.fail_centroid:
            raise RuntimeError("centroid write failed")
        self.centroid_updates.append(kwargs)


class FakeGraphStore:
    domain = "s2p"

    def __init__(self) -> None:
        self.decisions: dict[str, dict[str, object]] = {
            "S2P-DK-1": _decision(),
        }

    def get_decision(
        self,
        decision_id: str,
        domain: str | None = None,
    ) -> dict[str, object] | None:
        if domain is not None:
            assert domain == self.domain
        return self.decisions.get(decision_id)

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict[str, object] | None = None,
        domain: str | None = None,
    ) -> None:
        raise AssertionError("outcome writes are not part of this double")

    def get_archived_decisions(self, domain: str) -> list[dict[str, object]]:
        assert domain == self.domain
        return []


class FakeDKScorer:
    def __init__(self, weights: list[list[float]] | None = None) -> None:
        self.graph_store = FakeGraphStore()
        self.weights = weights
        self.reestimate_calls = 0
        self.get_weight_calls = 0

    def reestimate_dk_if_due(self) -> bool:
        self.reestimate_calls += 1
        return self.weights is not None

    def get_dk_weights(self) -> list[list[float]] | None:
        self.get_weight_calls += 1
        if self.weights is None:
            return None
        return [row[:] for row in self.weights]


class FakeCentroidScorer(FakeDKScorer):
    def __init__(self, phase: str = "MEAN_CONVERGENCE") -> None:
        super().__init__(weights=None)
        self.phase = phase
        self.centroids = {
            ("price_variance", "auto_approve"): [0.2, 0.4, 0.6],
        }

    def get_category_phase(self, category: str) -> str:
        assert category == "price_variance"
        return self.phase

    def get_centroid(self, category: str, action: str) -> list[float] | None:
        value = self.centroids.get((category, action))
        return None if value is None else list(value)

    def apply_learn(self, actual_action: str) -> None:
        if self.phase != "MEAN_CONVERGENCE":
            return
        before = self.centroids[("price_variance", actual_action)]
        self.centroids[("price_variance", actual_action)] = [
            before[0] + 0.3,
            before[1] + 0.4,
            before[2],
        ]


def _request(
    *,
    scorer: FakeDKScorer,
    learning_store: Any | None = None,
) -> SimpleNamespace:
    state = SimpleNamespace(scorer=scorer, graph_store=scorer.graph_store)
    if learning_store is not None:
        state.learning_store = learning_store
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _decision(vector: list[float] | None = None) -> dict[str, object]:
    return {
        "decision_id": "S2P-DK-1",
        "category": "price_variance",
        "recommended_action": "auto_approve",
        "factor_vector": vector or [0.9, 0.1, 0.2],
    }


def _reset_tracker(monkeypatch) -> DKWelfordTracker:
    tracker = DKWelfordTracker()
    monkeypatch.setattr(s2p_router, "_S2P_DK_WELFORD_TRACKER", tracker)
    return tracker


def _install_endpoint_state(
    monkeypatch,
    *,
    scorer: FakeDKScorer,
    learning_store: Any | None = None,
) -> None:
    monkeypatch.setattr(app.state, "scorer", scorer, raising=False)
    monkeypatch.setattr(app.state, "graph_store", scorer.graph_store, raising=False)
    if learning_store is None:
        if hasattr(app.state, "learning_store"):
            delattr(app.state, "learning_store")
    else:
        monkeypatch.setattr(app.state, "learning_store", learning_store, raising=False)
    monkeypatch.setattr(s2p_router, "_receipt_conservation_snapshot", lambda request: {}, raising=False)
    monkeypatch.setattr(
        s2p_router,
        "_append_evidence_receipt_before_outcome",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(s2p_router, "_record_outcome_receipt", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(s2p_router, "_record_supplier_profile", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(s2p_router, "_record_evolver_outcome_if_allowed", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(s2p_router, "_record_outcome_shadow", lambda *args, **kwargs: None, raising=False)

    def _fake_learn_with_scorer(scorer, decision_id, actual_action, outcome, context=None):
        apply_learn = getattr(scorer, "apply_learn", None)
        if callable(apply_learn):
            apply_learn(actual_action)
        return {
            "decision_id": decision_id,
            "status": "applied",
            "reward": 1.0,
            "reward_raw": 1.0,
        }

    monkeypatch.setattr(
        s2p_router,
        "_learn_with_scorer",
        _fake_learn_with_scorer,
        raising=False,
    )


def _learn_payload() -> dict[str, object]:
    return {
        "decision_id": "S2P-DK-1",
        "actual_action": "auto_approve",
        "outcome": "confirm",
    }


def _outcome_payload() -> dict[str, object]:
    return {
        "decision_id": "S2P-DK-1",
        "outcome": "confirm",
        "analyst_action": "auto_approve",
        "analyst_id": "A-DK",
        "factor_vector": [0.9, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        "category": "price_variance",
        "predicted_action": "auto_approve",
    }


def test_s2p_learn_persists_dk_weights_to_l5_after_phase_transition(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    store = RecordingDKLearningStore()
    request = _request(scorer=scorer, learning_store=store)

    s2p_router._persist_l5_dk_state(
        request,
        decision=_decision(),
        actual_action="auto_approve",
        payload={"status": "applied"},
    )

    assert scorer.reestimate_calls == 1
    assert len(store.updates) == 1
    update = store.updates[0]
    assert update["domain"] == "s2p"
    assert update["weight_tensor"] == [[0.2, 0.8, 0.4]]
    assert update["n_decisions_used"] == 1


def test_s2p_outcome_persists_dk_weights_to_l5_after_phase_transition(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    store = RecordingDKLearningStore()
    request = _request(scorer=scorer, learning_store=store)

    s2p_router._persist_l5_dk_state(
        request,
        decision=_decision(),
        actual_action="investigate",
        payload={"status": "applied", "outcome": "override"},
    )

    assert len(store.updates) == 1
    assert store.updates[0]["n_overridden"] == 1


def test_s2p_dk_includes_welford_state(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    store = RecordingDKLearningStore()
    request = _request(scorer=scorer, learning_store=store)

    s2p_router._persist_l5_dk_state(
        request,
        decision=_decision([1.0, 0.0, 0.5]),
        actual_action="auto_approve",
        payload={"status": "applied"},
    )

    update = store.updates[0]
    state = update["welford_state"]
    assert isinstance(state, dict)
    assert set(state) == {
        "confirmed_mean",
        "confirmed_m2",
        "overridden_mean",
        "overridden_m2",
        "all_mean",
        "all_m2",
        "n_all",
    }
    assert state["n_all"] == 1
    assert update["n_confirmed"] == 1
    assert update["n_overridden"] == 0


def test_s2p_dk_no_store_still_reestimates_runtime_dk(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    request = _request(scorer=scorer)

    s2p_router._persist_l5_dk_state(
        request,
        decision=_decision(),
        actual_action="auto_approve",
        payload={"status": "applied"},
    )

    assert scorer.reestimate_calls == 1


def test_s2p_dk_persist_failure_nonfatal(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    store = RecordingDKLearningStore(fail=True)
    request = _request(scorer=scorer, learning_store=store)

    s2p_router._persist_l5_dk_state(
        request,
        decision=_decision(),
        actual_action="auto_approve",
        payload={"status": "applied"},
    )

    assert scorer.reestimate_calls == 1


def test_s2p_response_shape_unchanged(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    store = RecordingDKLearningStore()
    request = _request(scorer=scorer, learning_store=store)
    payload = {"status": "applied", "decision_id": "S2P-DK-1"}
    before = dict(payload)

    s2p_router._persist_l5_dk_state(
        request,
        decision=_decision(),
        actual_action="auto_approve",
        payload=payload,
    )

    assert payload == before


def test_s2p_dk_not_written_before_variance_phase(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=None)
    store = RecordingDKLearningStore()
    request = _request(scorer=scorer, learning_store=store)

    s2p_router._persist_l5_dk_state(
        request,
        decision=_decision(),
        actual_action="auto_approve",
        payload={"status": "applied"},
    )

    assert scorer.reestimate_calls == 1
    assert store.updates == []


def test_s2p_learn_endpoint_no_store_still_reestimates_runtime_dk(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    _install_endpoint_state(monkeypatch, scorer=scorer)

    response = TestClient(app).post("/api/learn", json=_learn_payload())

    assert response.status_code == 200
    assert scorer.reestimate_calls == 1


def test_s2p_outcome_endpoint_persists_dk_when_store_exists(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    store = RecordingDKLearningStore()
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    response = TestClient(app).post("/api/s2p/outcome", json=_outcome_payload())

    assert response.status_code == 200
    payload = response.json()
    assert "welford_state" not in payload
    assert len(store.updates) == 1
    update = store.updates[0]
    assert update["domain"] == "s2p"
    assert update["weight_tensor"] == [[0.2, 0.8, 0.4]]
    assert isinstance(update["welford_state"], dict)


def test_s2p_endpoint_l5_failure_nonfatal(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=[[0.2, 0.8, 0.4]])
    store = RecordingDKLearningStore(fail=True)
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    response = TestClient(app).post("/api/s2p/outcome", json=_outcome_payload())

    assert response.status_code == 200
    assert scorer.reestimate_calls == 1


def test_s2p_endpoint_no_weight_before_variance_no_l5_write(monkeypatch) -> None:
    _reset_tracker(monkeypatch)
    scorer = FakeDKScorer(weights=None)
    store = RecordingDKLearningStore()
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    response = TestClient(app).post("/api/s2p/outcome", json=_outcome_payload())

    assert response.status_code == 200
    assert scorer.reestimate_calls == 1
    assert store.updates == []


def test_s2p_learn_persists_centroid_to_l5_in_mean_convergence(monkeypatch) -> None:
    scorer = FakeCentroidScorer()
    store = RecordingCentroidLearningStore()
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    response = TestClient(app).post("/api/learn", json=_learn_payload())

    assert response.status_code == 200
    assert len(store.centroid_updates) == 1
    update = store.centroid_updates[0]
    assert update["domain"] == "s2p"
    assert update["category"] == "price_variance"
    assert update["action"] == "auto_approve"
    assert update["centroid_vector"] == [0.5, 0.8, 0.6]
    assert update["delta_norm"] == 0.5
    assert update["caused_by_decision_id"] == "S2P-DK-1"
    assert "centroid_vector" not in response.json()


def test_s2p_outcome_persists_centroid_to_l5_if_path_updates_centroid(monkeypatch) -> None:
    scorer = FakeCentroidScorer()
    store = RecordingCentroidLearningStore()
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    response = TestClient(app).post("/api/s2p/outcome", json=_outcome_payload())

    assert response.status_code == 200
    assert len(store.centroid_updates) == 1
    assert store.centroid_updates[0]["centroid_vector"] == [0.5, 0.8, 0.6]


def test_s2p_centroid_l5_nonfatal(monkeypatch) -> None:
    scorer = FakeCentroidScorer()
    store = RecordingCentroidLearningStore(fail=True)
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    response = TestClient(app).post("/api/learn", json=_learn_payload())

    assert response.status_code == 200
    assert store.centroid_updates == []


def test_s2p_centroid_l5_skipped_in_variance_learning(monkeypatch) -> None:
    scorer = FakeCentroidScorer(phase="VARIANCE_LEARNING")
    store = RecordingCentroidLearningStore()
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    response = TestClient(app).post("/api/learn", json=_learn_payload())

    assert response.status_code == 200
    assert store.centroid_updates == []


def test_s2p_centroid_response_shape_unchanged(monkeypatch) -> None:
    scorer = FakeCentroidScorer()
    store = RecordingCentroidLearningStore()
    _install_endpoint_state(monkeypatch, scorer=scorer, learning_store=store)

    payload = TestClient(app).post("/api/learn", json=_learn_payload()).json()

    assert "centroid_vector" not in payload
    assert "delta_norm" not in payload
