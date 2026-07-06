import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.routers import s2p as s2p_router
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.scoring.dk_persistence import DKWelfordTracker


DOMAIN = "s2p"
CATEGORY = "price_variance"
FACTORS = tuple(S2PDomainConfig.factors)
ACTIONS = (
    "auto_approve",
    "hold_for_review",
    "escalate_to_buyer",
    "flag_leakage",
    "refer_to_specialist",
)


class FullFlowGraphStore(InMemoryGraphStore):
    def count_categories_with_n(self, domain: str, n: int) -> int:
        counts: dict[str, int] = {}
        for decision in self.get_all_decisions(domain):
            category = decision.get("category")
            if category is not None:
                counts[str(category)] = counts.get(str(category), 0) + 1
        return sum(1 for count in counts.values() if count >= int(n))


def _factor_payload(i: int) -> dict[str, float]:
    if i % 2 == 0:
        values = [0.95, 0.9, 0.1, 0.1, 0.5, 0.5, 0.5, 0.5]
    else:
        values = [0.05, 0.1, 0.9, 0.8, 0.5, 0.5, 0.5, 0.5]
    return dict(zip(FACTORS, values))


def _assert_finite_vector(vector: list[float], expected_len: int) -> None:
    assert isinstance(vector, list)
    assert len(vector) == expected_len
    assert all(math.isfinite(float(value)) for value in vector)


def _score_direct(scorer, i: int) -> dict:
    return scorer.score(
        factors=_factor_payload(i),
        category=CATEGORY,
        metadata={"invoice_id": f"INV-P27-{i:04d}"},
    )


def _learn(client: TestClient, decision_id: str, actual_action: str, *, outcome: str = "confirm") -> dict:
    response = client.post(
        "/api/learn",
        json={
            "decision_id": decision_id,
            "actual_action": actual_action,
            "outcome": outcome,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_s2p_full_learn_flow_writes_all_three_l5_state_types(monkeypatch) -> None:
    store = FullFlowGraphStore(domain=DOMAIN)
    scorer = build_s2p_scorer(graph_store=store)
    tracker = DKWelfordTracker()

    monkeypatch.setattr(app.state, "scorer", scorer, raising=False)
    monkeypatch.setattr(app.state, "graph_store", store, raising=False)
    monkeypatch.setattr(app.state, "learning_store", store, raising=False)
    monkeypatch.setattr(s2p_router, "_S2P_DK_WELFORD_TRACKER", tracker)
    monkeypatch.setattr(s2p_router, "_receipt_conservation_snapshot", lambda request: {}, raising=False)
    monkeypatch.setattr(s2p_router, "_append_evidence_receipt_before_outcome", lambda *args, **kwargs: None)
    monkeypatch.setattr(s2p_router, "_record_outcome_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(s2p_router, "_record_supplier_profile", lambda *args, **kwargs: None)
    monkeypatch.setattr(s2p_router, "_record_evolver_outcome_if_allowed", lambda *args, **kwargs: None)
    monkeypatch.setattr(s2p_router, "_record_outcome_shadow", lambda *args, **kwargs: None)

    client = TestClient(app)

    first_score = _score_direct(scorer, 0)
    first_learn = _learn(client, first_score.decision_id, first_score.action)
    assert scorer.get_category_phase(CATEGORY) == "MEAN_CONVERGENCE"

    last_mean_score = first_score
    for i in range(1, 200):
        last_mean_score = _score_direct(scorer, i)
        _learn(client, last_mean_score.decision_id, last_mean_score.action)

    assert scorer.get_category_phase(CATEGORY) == "VARIANCE_LEARNING"
    centroids_before_variance = store.get_centroids(DOMAIN)
    assert centroids_before_variance

    dk_row = None
    final_learn = first_learn
    for i in range(200, 280):
        score = _score_direct(scorer, i)
        actual_action = score.action
        final_learn = _learn(
            client,
            score.decision_id,
            actual_action,
        )
        dk_row = store.get_dk_weights(DOMAIN)
        if dk_row:
            break

    assert dk_row is not None
    assert store.get_centroids(DOMAIN) == centroids_before_variance

    centroid = next(
        row for row in centroids_before_variance if row["category"] == CATEGORY and row["action"] == last_mean_score.action
    )
    _assert_finite_vector(centroid["vector_json"], len(FACTORS))
    assert isinstance(centroid["caused_by_decision_id"], str)
    assert centroid["caused_by_decision_id"]

    welford_state = dk_row["welford_state"]
    assert welford_state is not None
    for key in (
        "confirmed_mean",
        "confirmed_m2",
        "overridden_mean",
        "overridden_m2",
        "all_mean",
        "all_m2",
    ):
        _assert_finite_vector(welford_state[key], len(FACTORS))
    assert welford_state["n_all"] > 0
    assert dk_row["n_confirmed"] >= 0
    assert dk_row["n_overridden"] >= 0
    assert dk_row["weight_json"] == scorer.get_dk_weights()

    conservation = store.get_conservation_state(DOMAIN)
    assert conservation is not None
    assert conservation["domain"] == DOMAIN
    assert math.isfinite(float(conservation["alpha"]))

    for payload in (first_learn, final_learn):
        assert "decision_id" in payload
        assert "outcome" in payload
        assert "decisions_total" in payload
        assert "centroid_vector" not in payload
        assert "weight_tensor" not in payload
        assert "welford_state" not in payload
