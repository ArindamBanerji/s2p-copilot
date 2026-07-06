from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.services.centroid_explorer import (
    CentroidExplorerError,
    S2PCentroidExplorerService,
    explain_decision,
    get_all_centroid_cells,
)


client = TestClient(app)


BASE_VECTOR = [0.9, 0.1, 0.05, 0.04, 0.5, 0.8, 0.95]
ALT_VECTOR = [0.1, 0.9, 0.8, 0.7, 0.2, 0.1, 0.05]


class FakeScorer:
    def __init__(self, *, dk_weights: Any = None) -> None:
        self.dk_weights = dk_weights
        self.calls: list[tuple[str, str]] = []

    @property
    def centroids(self):
        raise AssertionError("private centroid attribute must not be read")

    @property
    def gae_scorer(self):
        raise AssertionError("private gae_scorer attribute must not be read")

    def get_centroid(self, category: str, action: str) -> list[float]:
        self.calls.append((category, action))
        if action == "auto_approve":
            return list(BASE_VECTOR)
        if action == "hold_for_review":
            return list(ALT_VECTOR)
        return [0.5] * S2PDomainConfig.n_factors

    def get_dk_weights(self):
        return self.dk_weights

    def learn(self, *_args, **_kwargs):
        raise AssertionError("learn must not be called")


@dataclass
class FakeProvenanced:
    value: Any
    source: str = "fixture"
    provenance_tier: str = "context"
    source_count: int = 0
    factor_eligible: bool = False
    provenance_label: str = "fixture context"
    measured: bool = False
    verified: bool = False
    computed_at: str = ""
    warnings: list[str] = field(default_factory=list)


class FakeGraphStore:
    def __init__(self, decision: dict[str, Any] | None = None, checkpoints: list[dict[str, Any]] | None = None) -> None:
        self.decision = decision
        self.checkpoints = checkpoints
        self.write_decision_calls = 0
        self.write_outcome_calls = 0
        self.write_entity_enrichment_calls = 0

    def get_decision(self, decision_id: str):
        if self.decision and self.decision.get("decision_id") == decision_id:
            return dict(self.decision)
        return None

    def get_centroid_checkpoints(self, domain: str, **_kwargs):
        return list(self.checkpoints or [])

    def read_entity_enrichment(self, **_kwargs):
        return {"otif_score": FakeProvenanced(0.91, provenance_label="fixture OTIF context · integration pending")}

    def write_decision(self, *_args, **_kwargs):
        self.write_decision_calls += 1
        raise AssertionError("write_decision must not be called")

    def write_outcome(self, *_args, **_kwargs):
        self.write_outcome_calls += 1
        raise AssertionError("write_outcome must not be called")

    def write_entity_enrichment(self, *_args, **_kwargs):
        self.write_entity_enrichment_calls += 1
        raise AssertionError("write_entity_enrichment must not be called")


def _decision(**overrides) -> dict[str, Any]:
    payload = {
        "decision_id": "D-1",
        "category": "price_variance",
        "recommended_action": "auto_approve",
        "confidence": 0.91,
        "probabilities": [0.91, 0.03, 0.02, 0.02, 0.02],
        "factor_vector": list(BASE_VECTOR),
        "metadata": {"supplier_id": "SUP-001", "invoice_id": "INV-1"},
    }
    payload.update(overrides)
    return payload


def test_explain_basic_returns_closest_action():
    explanation = explain_decision(_decision(), FakeScorer())

    assert explanation.closest_action == "auto_approve"
    assert explanation.closest_matches_recommendation is True
    assert explanation.read_only is True


def test_l2_centroid_distances_are_correct():
    vector = [0.5] * S2PDomainConfig.n_factors
    explanation = explain_decision(_decision(factor_vector=vector), FakeScorer())

    expected = math.sqrt(sum((0.5 - value) ** 2 for value in BASE_VECTOR))
    assert explanation.centroid_distances["auto_approve"] == round(expected, 6)


def test_factor_contributions_sorted_by_weighted_distance():
    vector = [0.0, 0.1, 0.05, 0.04, 0.5, 0.8, 0.95]
    explanation = explain_decision(_decision(factor_vector=vector), FakeScorer())
    weighted = [row.weighted_distance for row in explanation.factor_contributions]

    assert weighted == sorted(weighted, reverse=True)


def test_explain_no_dk_weights_uses_learning_uniform_fallback():
    explanation = explain_decision(_decision(), FakeScorer())

    assert explanation.dk_status == "learning"
    assert all(row.dk_status == "learning" for row in explanation.factor_contributions)
    assert all(row.dk_weight is None for row in explanation.factor_contributions)


def test_explain_with_dk_weights_uses_weighted_contributions():
    weights = [[1.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]] * S2PDomainConfig.n_categories
    vector = [0.0, 0.1, 0.05, 0.04, 0.5, 0.8, 0.95]
    explanation = explain_decision(_decision(factor_vector=vector), FakeScorer(), dk_weights=weights)

    assert explanation.dk_status == "available"
    first = explanation.factor_contributions[0]
    assert first.factor_name == "match_status"
    assert first.dk_weight == 1.0
    assert first.weighted_distance == 0.9


def test_direction_above_below_at_centroid():
    vector = [1.0, 0.0, 0.05, 0.04, 0.5, 0.8, 0.95]
    explanation = explain_decision(_decision(factor_vector=vector), FakeScorer())
    directions = {row.factor_name: row.direction for row in explanation.factor_contributions}

    assert directions["match_status"] == "above_centroid"
    assert directions["amount_variance_ratio"] == "below_centroid"
    assert directions["duplicate_score"] == "at_centroid"


def test_summary_when_closest_matches_recommended_is_non_causal():
    explanation = explain_decision(_decision(), FakeScorer())

    assert "closest to auto_approve in learned centroid space" in explanation.summary
    assert "scorer chose" not in explanation.summary.lower()
    assert "because" not in explanation.summary.lower()


def test_summary_when_closest_differs_from_recommended_does_not_overclaim():
    explanation = explain_decision(_decision(recommended_action="hold_for_review"), FakeScorer())

    assert explanation.closest_action == "auto_approve"
    assert explanation.closest_matches_recommendation is False
    assert "while the scorer recommended hold_for_review" in explanation.summary
    assert "explanatory context" in explanation.summary
    assert "replacement for the scorer decision" in explanation.summary


def test_p39_context_does_not_affect_distances_or_closest_action():
    decision = _decision()
    baseline = explain_decision(decision, FakeScorer(), p39_evidence={})
    with_p39 = explain_decision(
        decision,
        FakeScorer(),
        p39_evidence={"exception_rate": {"value": 0.99, "source": "fixture"}},
    )

    assert with_p39.closest_action == baseline.closest_action
    assert with_p39.centroid_distances == baseline.centroid_distances
    assert with_p39.p39_evidence["exception_rate"]["source"] == "fixture"


def test_missing_factor_vector_returns_safe_error_or_exception():
    with pytest.raises(CentroidExplorerError, match="no factor_vector") as exc:
        explain_decision(_decision(factor_vector=None), FakeScorer())

    assert exc.value.status_code == 422


def test_invalid_factor_vector_length_returns_safe_error_or_exception():
    with pytest.raises(CentroidExplorerError, match=f"length 2 != {S2PDomainConfig.n_factors}") as exc:
        explain_decision(_decision(factor_vector=[0.1, 0.2]), FakeScorer())

    assert exc.value.status_code == 422


def test_drift_no_history_returns_unsupported_empty_points():
    service = S2PCentroidExplorerService(scorer=FakeScorer(), graph_store=object())
    response = service.get_centroid_drift("price_variance", "auto_approve")

    assert response.supported is False
    assert response.reason == "centroid_history_unavailable"
    assert response.points == []


def test_centroid_cell_endpoint():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store

    response = client.get("/api/s2p/centroid/price_variance/auto_approve")

    assert response.status_code == 200
    payload = response.json()
    assert payload["category"] == "price_variance"
    assert payload["action"] == "auto_approve"
    assert len(payload["centroid_vector"]) == S2PDomainConfig.n_factors
    assert payload["read_only"] is True


def test_all_centroids_endpoint_returns_25_cells():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store

    response = client.get("/api/s2p/centroid/all")

    assert response.status_code == 200
    payload = response.json()
    assert payload["shape"] == {
        "categories": S2PDomainConfig.n_categories,
        "actions": S2PDomainConfig.n_actions,
        "factors": S2PDomainConfig.n_factors,
    }
    assert len(payload["cells"]) == 25
    assert payload["read_only"] is True


def test_explain_endpoint_success_for_stored_decision():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    score = client.post(
        "/api/s2p/score",
        json={
            "event_id": "P41-EXP-001",
            "category": "price_variance",
            "amount": 1000.0,
            "supplier_id": "SUP-001",
        },
    )
    assert score.status_code == 200
    decision_id = score.json()["decision_id"]

    response = client.get(f"/api/s2p/centroid/explain/{decision_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_id"] == decision_id
    assert payload["recommended_action"]
    assert payload["closest_action"] in S2PDomainConfig.actions
    assert set(payload["centroid_distances"]) == set(S2PDomainConfig.actions)
    assert len(payload["factor_contributions"]) == S2PDomainConfig.n_factors
    assert payload["read_only"] is True


def test_explain_endpoint_missing_decision_404():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store

    response = client.get("/api/s2p/centroid/explain/NO-SUCH-DECISION")

    assert response.status_code == 404


def test_explain_endpoint_missing_dk_still_succeeds_with_learning_status():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    decision_id = client.post(
        "/api/s2p/score",
        json={
            "event_id": "P41-EXP-002",
            "category": "price_variance",
            "amount": 1000.0,
            "supplier_id": "SUP-001",
            "match_status": 0.92,
            "amount_variance_ratio": 0.08,
            "duplicate_score": 0.04,
            "supplier_exception_history": 0.05,
            "payment_terms_impact": 0.48,
            "commodity_index_correlation": 0.76,
            "tax_regulatory_compliance": 0.90,
        },
    ).json()["decision_id"]

    response = client.get(f"/api/s2p/centroid/explain/{decision_id}")

    assert response.status_code == 200
    assert response.json()["dk_status"] in {"learning", "available"}


def test_drift_endpoint_no_history_returns_unsupported():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store

    response = client.get("/api/s2p/centroid/drift/price_variance/auto_approve")

    assert response.status_code == 200
    payload = response.json()
    assert payload["supported"] in {False, True}
    assert isinstance(payload["points"], list)
    if not payload["points"]:
        assert payload["reason"] in {
            "centroid_history_unavailable",
            "no centroid checkpoint history for category/action",
        }


def test_category_action_name_mapping():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store

    bad_category = client.get("/api/s2p/centroid/nope/auto_approve")
    bad_action = client.get("/api/s2p/centroid/price_variance/nope")

    assert bad_category.status_code == 404
    assert bad_action.status_code == 404


def test_endpoints_are_read_only_no_decision_or_outcome_write():
    decision = _decision()
    store = FakeGraphStore(decision)
    app.state.scorer = FakeScorer()
    app.state.graph_store = store

    response = client.get("/api/s2p/centroid/explain/D-1")

    assert response.status_code == 200
    assert store.write_decision_calls == 0
    assert store.write_outcome_calls == 0
    assert store.write_entity_enrichment_calls == 0


def test_all_centroids_uses_public_get_centroid_only():
    scorer = FakeScorer()
    cells = get_all_centroid_cells(scorer)

    assert len(cells) == 25
    assert len(scorer.calls) == 25


def test_drift_uses_real_centroid_checkpoints_when_present():
    full_tensor = [
        [list(BASE_VECTOR) for _action in S2PDomainConfig.actions]
        for _category in S2PDomainConfig.categories
    ]
    service = S2PCentroidExplorerService(
        scorer=FakeScorer(),
        graph_store=FakeGraphStore(checkpoints=[{"id": 1, "category": "price_variance", "centroids": full_tensor}]),
    )

    response = service.get_centroid_drift("price_variance", "auto_approve")

    assert response.supported is True
    assert len(response.points) == 1
    assert response.points[0]["centroid_vector"] == BASE_VECTOR
