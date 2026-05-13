"""
tests/test_s2p_score_endpoint.py — POST /api/s2p/score endpoint tests.

Run from backend/:
    pytest tests/test_s2p_score_endpoint.py -v
"""

import json
import sys
import os
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app, build_s2p_scorer
from app.domains.s2p.config import S2PDomainConfig
from app.routers import s2p as s2p_router

client = TestClient(app)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

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
    return app.state.scorer


def score_for_learn(action_payload=None):
    reset_sdk_scorer()
    response = client.post("/api/s2p/score", json={**VALID_REQUEST, **(action_payload or {})})
    assert response.status_code == 200
    return response.json()


def test_score_endpoint_returns_200():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    assert response.status_code == 200


def test_score_response_has_required_fields():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    for key in ("event_id", "category", "action", "action_index",
                "confidence", "probabilities", "factor_vector", "factor_names"):
        assert key in data, f"Missing key: {key}"


def test_score_action_is_valid_s2p_action():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    action = response.json()["action"]
    assert action in [
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    ]


def test_score_factor_vector_length():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    assert len(data["factor_vector"]) == 7
    assert len(data["factor_names"]) == 7
    assert data["factor_names"] == [
        "match_status",
        "amount_variance_ratio",
        "duplicate_score",
        "supplier_exception_history",
        "payment_terms_impact",
        "commodity_index_correlation",
        "tax_regulatory_compliance",
    ]


def test_score_invalid_category_returns_422():
    bad_request = {**VALID_REQUEST, "category": "lateral_movement"}
    response = client.post("/api/s2p/score", json=bad_request)
    assert response.status_code == 422


def test_score_legacy_category_returns_422():
    bad_request = {**VALID_REQUEST, "category": "supplier_risk"}
    response = client.post("/api/s2p/score", json=bad_request)
    assert response.status_code == 422


def test_score_endpoint_uses_compute_all_factors(monkeypatch):
    calls = []
    known = {name: (idx + 1) / 10 for idx, name in enumerate(S2PDomainConfig.factors)}

    def fake_compute_all_factors(invoice, context=None):
        calls.append((invoice, context))
        return known

    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][1] is None
    assert response.json()["factor_vector"] == [
        known[name] for name in S2PDomainConfig.factors
    ]


def test_score_endpoint_uses_graph_context_when_available(monkeypatch):
    calls = []

    class FakeGraphStore:
        def query_context(self, invoice_id, hops):
            assert invoice_id == VALID_REQUEST["event_id"]
            assert hops == 2
            return [{"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}}]

    def fake_compute_all_factors(invoice, context=None):
        calls.append(context)
        return {name: 0.2 for name in S2PDomainConfig.factors}

    app.state.graph_store = FakeGraphStore()
    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)
    try:
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
    finally:
        del app.state.graph_store

    assert response.status_code == 200
    assert calls == [{"neighbors": [{"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}}]}]


def test_score_endpoint_graph_context_failure_falls_back(monkeypatch):
    calls = []

    class FailingGraphStore:
        def query_context(self, invoice_id, hops):
            raise RuntimeError("graph unavailable")

    def fake_compute_all_factors(invoice, context=None):
        calls.append(context)
        return {name: 0.3 for name in S2PDomainConfig.factors}

    app.state.graph_store = FailingGraphStore()
    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)
    try:
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
    finally:
        del app.state.graph_store

    assert response.status_code == 200
    assert calls == [None]


def test_score_endpoint_uses_fixture_invoice_factors_when_no_graph():
    invoice = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))[0]
    payload = {
        "event_id": invoice["invoice_id"],
        "category": invoice["category"],
        "amount": invoice["amount"],
        "supplier_id": invoice["supplier_id"],
    }

    response = client.post("/api/s2p/score", json=payload)

    assert response.status_code == 200
    assert response.json()["factor_vector"] == [
        invoice["factors"][name] for name in S2PDomainConfig.factors
    ]


def test_score_endpoint_graph_lookup_uses_fixture_invoice_id(monkeypatch):
    invoice = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))[0]
    seen = []

    class FakeGraphStore:
        def query_context(self, invoice_id, hops):
            seen.append((invoice_id, hops))
            return []

    app.state.graph_store = FakeGraphStore()
    try:
        response = client.post(
            "/api/s2p/score",
            json={
                "event_id": invoice["invoice_id"],
                "category": invoice["category"],
                "amount": invoice["amount"],
                "supplier_id": invoice["supplier_id"],
            },
        )
    finally:
        del app.state.graph_store

    assert response.status_code == 200
    assert seen == [(invoice["invoice_id"], 2)]


def test_reward_function_wired_in_scorer():
    reset_sdk_scorer()
    reward_function = getattr(app.state, "s2p_reward_function", None)

    assert reward_function is not None
    assert reward_function.name == "s2p_graded_financial"
    assert reward_function.compute("auto_approve", "auto_approve", {}) == 1.0
    assert app.state.scorer._reward_fn is reward_function


def test_sdk_learn_route_exists_and_returns_reward_fields():
    scored = score_for_learn()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reward"] == 0.8
    assert data["reward_raw"] == 0.8


def test_conservation_status_endpoint_exists():
    reset_sdk_scorer()

    response = client.get("/api/conservation/status")

    assert response.status_code == 200
    assert response.json()["domain"] == "s2p"


def test_score_includes_process_context_when_available(monkeypatch):
    monkeypatch.setattr(
        s2p_router,
        "_load_celonis_cache",
        lambda: {
            "activities": [
                {
                    "id": "match_invoice_to_gr",
                    "name": "Match Invoice to GR",
                    "avg_duration_hours": 42.0,
                    "bottleneck": True,
                    "bottleneck_cause": "MATKL_V2",
                }
            ]
        },
    )

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    process_context = response.json()["process_context"]
    assert process_context["bottleneck_activity"] == "Match Invoice to GR"
    assert process_context["duration_median_min"] == 2520.0
    assert process_context["cause"] == "MATKL_V2"
    assert process_context["source"] == "celonis_cache"


def test_score_omits_process_context_when_unavailable(monkeypatch):
    monkeypatch.setattr(s2p_router, "_load_celonis_cache", lambda: {})

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["process_context"] is None


def test_outcome_returns_reward_fields():
    scored = score_for_learn()
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
            "amount": 1000,
            "at_risk": 1000,
            "recovery_pct": 80,
        },
    )

    assert response.status_code == 200
    assert response.json()["reward"] == 0.8
    assert response.json()["reward_raw"] == 0.8


def test_outcome_confirmed_positive_reward():
    scored = score_for_learn()
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
        },
    )

    assert response.status_code == 200
    assert response.json()["reward"] > 0


def test_outcome_overridden_negative_reward():
    scored = score_for_learn()
    override_action = next(action for action in S2PDomainConfig.actions if action != scored["action"])
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "override",
            "analyst_action": override_action,
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
            "amount": 1000,
            "at_risk": 250,
        },
    )

    assert response.status_code == 200
    assert response.json()["reward_raw"] == -0.25
    assert response.json()["reward"] == -1.25


def test_learn_with_scorer_restores_reward_function_on_exception():
    class Reward:
        def compute(self, recommended_action, actual_action, outcome):
            return float(outcome.get("recovery_pct", 100)) / 100

    class FailingScorer:
        def __init__(self):
            self._reward_fn = Reward()

        def learn(self, decision_id, actual_action, outcome):
            raise RuntimeError("boom")

    scorer = FailingScorer()
    original = scorer._reward_fn

    try:
        s2p_router._learn_with_scorer(
            scorer,
            "S2P-ERR",
            "auto_approve",
            "confirmed",
            {"recovery_pct": 25},
        )
    except RuntimeError:
        pass

    assert scorer._reward_fn is original


def test_learn_context_isolated_between_requests():
    first = score_for_learn()
    first_response = client.post(
        "/api/learn",
        json={
            "decision_id": first["decision_id"],
            "actual_action": first["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 25},
        },
    )

    second = score_for_learn()
    second_response = client.post(
        "/api/learn",
        json={
            "decision_id": second["decision_id"],
            "actual_action": second["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 90},
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["reward_raw"] == 0.25
    assert second_response.json()["reward_raw"] == 0.9


def test_reward_context_lock_serializes_mutation(monkeypatch):
    class RecordingLock:
        def __init__(self):
            self._lock = threading.Lock()
            self.assignment_locked = []

        def __enter__(self):
            self._lock.acquire()
            return self

        def __exit__(self, exc_type, exc, tb):
            self._lock.release()

        def locked(self):
            return self._lock.locked()

    class Reward:
        def compute(self, recommended_action, actual_action, outcome):
            return float(outcome.get("recovery_pct", 100)) / 100

    class RecordingScorer:
        def __init__(self, lock):
            object.__setattr__(self, "lock", lock)
            object.__setattr__(self, "_reward_fn", Reward())
            object.__setattr__(self, "assignment_locked", [])

        def __setattr__(self, name, value):
            if name == "_reward_fn":
                self.assignment_locked.append(self.lock.locked())
            object.__setattr__(self, name, value)

        def learn(self, decision_id, actual_action, outcome):
            return {
                "decision_id": decision_id,
                "reward": self._reward_fn.compute("auto_approve", actual_action, {}),
                "reward_raw": self._reward_fn.compute("auto_approve", actual_action, {}),
            }

    lock = RecordingLock()
    scorer = RecordingScorer(lock)
    monkeypatch.setattr(s2p_router, "_reward_context_lock", lock)

    result = s2p_router._learn_with_scorer(
        scorer,
        "S2P-LOCK",
        "auto_approve",
        "confirmed",
        {"recovery_pct": 40},
    )

    assert result["reward_raw"] == 0.4
    assert scorer.assignment_locked == [True, True]
