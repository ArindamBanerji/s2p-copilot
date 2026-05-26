"""
tests/test_learn_conservation_guard.py - Rule #46 learn/evolver guard tests.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app, build_s2p_scorer
from app.routers import s2p as s2p_router
from app.services.s2p_evolver import reset_s2p_evolver
from app.services.supplier_profile_accumulator import accumulator as supplier_profile_accumulator


client = TestClient(app)

VALID_REQUEST = {
    "event_id": "E001",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-001",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    reset_s2p_evolver()
    supplier_profile_accumulator.reset()
    return app.state.scorer


def score_for_learn() -> dict:
    reset_sdk_scorer()
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    assert response.status_code == 200
    return response.json()


def learn_payload(score: dict, **overrides) -> dict:
    payload = {
        "decision_id": score["decision_id"],
        "actual_action": score["action"],
        "outcome": "confirmed",
        "context": {"analyst": "rule46-test"},
    }
    payload.update(overrides)
    return payload


def active_variant_id(score: dict) -> str:
    active_variant = score.get("active_variant") or {}
    variant_id = active_variant.get("id")
    assert variant_id
    return variant_id


def test_learn_returns_200_when_conservation_not_green(monkeypatch):
    score = score_for_learn()
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "RED")

    response = client.post("/api/learn", json=learn_payload(score))

    assert s2p_router._is_learning_paused("RED") is True
    assert response.status_code == 200
    assert response.status_code != 422


def test_learn_without_variant_id_never_returns_422(monkeypatch):
    score = score_for_learn()
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "RED")

    response = client.post("/api/learn", json=learn_payload(score))

    assert response.status_code != 422
    assert response.status_code == 200
    data = response.json()
    assert data["evolution_recorded"] is False
    assert data["evolution_note"] == "variant_id not provided"


def test_learn_records_outcome_when_paused(monkeypatch):
    score = score_for_learn()
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "RED")

    response = client.post("/api/learn", json=learn_payload(score))

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["reward"], (int, float))
    assert data["invoice_id"] == VALID_REQUEST["event_id"]
    graph_store = app.state.graph_store
    assert graph_store.count_verified(getattr(graph_store, "domain", "s2p")) == 1


def test_evolver_not_called_when_paused(monkeypatch):
    score = score_for_learn()
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "RED")

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("record_triage_outcome should not run while learning is paused")

    monkeypatch.setattr(s2p_router, "record_triage_outcome", fail_if_called)

    response = client.post(
        "/api/learn",
        json=learn_payload(score, variant_id=active_variant_id(score)),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["evolution_recorded"] is False
    assert data["evolution_note"] == "learning paused by conservation"
    assert "active_variant_id" not in data


def test_evolver_called_when_not_paused_and_variant_id_present(monkeypatch):
    score = score_for_learn()
    calls = []
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "GREEN")

    def record_call(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(s2p_router, "record_triage_outcome", record_call)

    response = client.post(
        "/api/learn",
        json=learn_payload(score, variant_id=active_variant_id(score)),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["evolution_recorded"] is True
    assert data["active_variant_id"] == active_variant_id(score)
    assert len(calls) == 1
