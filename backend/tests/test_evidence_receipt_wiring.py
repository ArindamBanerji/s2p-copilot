"""S2P evidence receipt pre-outcome wiring tests."""

from __future__ import annotations

import json
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.routers import s2p as s2p_router
from app.services.receipt_store import reset_receipt_store
from app.services.s2p_evolver import reset_s2p_evolver
from app.services.supplier_profile_accumulator import accumulator as supplier_profile_accumulator


client = TestClient(app)


def _reset_app_state():
    reset_receipt_store()
    app.state.scorer = build_s2p_scorer(":memory:")
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    s2p_router._clear_score_conservation_status_cache()
    reset_s2p_evolver()
    supplier_profile_accumulator.reset()
    return app.state.scorer


def setup_function() -> None:
    _reset_app_state()


def teardown_function() -> None:
    _reset_app_state()


def _score(event_id: str) -> dict:
    response = client.post(
        "/api/s2p/score",
        json={
            "event_id": event_id,
            "category": "price_variance",
            "amount": 5000.0,
            "supplier_id": "SUP-EVIDENCE",
            "match_status": 0.92,
            "amount_variance_ratio": 0.08,
            "duplicate_score": 0.04,
            "supplier_exception_history": 0.05,
            "payment_terms_impact": 0.48,
            "commodity_index_correlation": 0.76,
            "tax_regulatory_compliance": 0.90,
        },
    )
    assert response.status_code == 200
    return response.json()


def _learn(decision_id: str, actual_action: str, outcome: str = "confirmed") -> dict:
    response = client.post(
        "/api/learn",
        json={
            "decision_id": decision_id,
            "actual_action": actual_action,
            "outcome": outcome,
        },
    )
    assert response.status_code == 200
    return response.json()


def _evidence_rows() -> list[dict]:
    return [
        dict(row)
        for row in app.state.graph_store.connection.execute(
            "SELECT * FROM evidence_receipts WHERE domain = ? ORDER BY chain_index",
            ("s2p",),
        ).fetchall()
    ]


def _outbox_rows() -> list[dict]:
    return [
        dict(row)
        for row in app.state.graph_store.connection.execute(
            "SELECT * FROM outbox WHERE domain = ? ORDER BY outbox_id",
            ("s2p",),
        ).fetchall()
    ]


def _outcome_count() -> int:
    return int(app.state.graph_store.connection.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0])


def test_learn_appends_evidence_receipt_before_outcome_write(monkeypatch) -> None:
    score = _score("EVID-RCP-ORDER-001")
    store = app.state.graph_store
    calls: list[str] = []
    original_append = store.append_evidence_receipt
    original_write_outcome = store.write_outcome

    def append_spy(**kwargs):
        calls.append("append")
        return original_append(**kwargs)

    def write_outcome_spy(*args, **kwargs):
        calls.append("write_outcome")
        return original_write_outcome(*args, **kwargs)

    monkeypatch.setattr(store, "append_evidence_receipt", append_spy)
    monkeypatch.setattr(store, "write_outcome", write_outcome_spy)

    _learn(score["decision_id"], score["action"])

    assert calls[:2] == ["append", "write_outcome"]
    rows = _evidence_rows()
    assert len(rows) == 1
    assert rows[0]["decision_id"] == score["decision_id"]
    assert rows[0]["receipt_intent_id"].startswith("RCP-")
    assert rows[0]["chain_index"] == 0
    assert rows[0]["payload_hash"]
    payload = json.loads(rows[0]["canonical_payload_json"])
    assert payload["decision_id"] == score["decision_id"]
    assert payload["domain"] == "s2p"
    assert payload["actual_action"] == score["action"]
    assert payload["confidence"] == score["confidence"]
    assert payload["category"] == score["category"]
    assert payload["factor_hash"]


def test_append_failure_without_outbox_prevents_outcome(monkeypatch) -> None:
    score = _score("EVID-RCP-BLOCK-001")
    store = app.state.graph_store
    before_outcomes = _outcome_count()

    def append_fails(**_kwargs):
        raise RuntimeError("append unavailable")

    def outbox_fails(**_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(store, "append_evidence_receipt", append_fails)
    monkeypatch.setattr(store, "enqueue_to_outbox", outbox_fails)

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 503
    assert _outcome_count() == before_outcomes
    assert _evidence_rows() == []
    assert _outbox_rows() == []


def test_append_failure_enqueues_outbox_then_writes_outcome(monkeypatch) -> None:
    score = _score("EVID-RCP-OUTBOX-001")
    store = app.state.graph_store
    calls: list[str] = []
    original_enqueue = store.enqueue_to_outbox
    original_write_outcome = store.write_outcome

    def append_fails(**_kwargs):
        calls.append("append")
        raise RuntimeError("append unavailable")

    def enqueue_spy(**kwargs):
        calls.append("outbox")
        return original_enqueue(**kwargs)

    def write_outcome_spy(*args, **kwargs):
        calls.append("write_outcome")
        return original_write_outcome(*args, **kwargs)

    monkeypatch.setattr(store, "append_evidence_receipt", append_fails)
    monkeypatch.setattr(store, "enqueue_to_outbox", enqueue_spy)
    monkeypatch.setattr(store, "write_outcome", write_outcome_spy)

    _learn(score["decision_id"], score["action"])

    assert calls[:3] == ["append", "outbox", "write_outcome"]
    assert _outcome_count() == 1
    assert _evidence_rows() == []
    outbox = _outbox_rows()
    assert len(outbox) == 1
    assert outbox[0]["operation_type"] == "append_evidence_receipt"
    assert outbox[0]["target_key"].startswith("s2p:RCP-")
    assert outbox[0]["causal_decision_id"] == score["decision_id"]
    payload = json.loads(outbox[0]["payload_json"])
    assert payload["receipt_intent_id"].startswith("RCP-")
    assert payload["domain"] == "s2p"
    assert payload["decision_id"] == score["decision_id"]
    assert payload["source_route"] == "/api/learn"
    assert payload["canonical_payload"]["factor_hash"]


def test_chain_index_monotonic_for_multiple_receipts() -> None:
    first = _score("EVID-RCP-CHAIN-001")
    second = _score("EVID-RCP-CHAIN-002")

    _learn(first["decision_id"], first["action"])
    _learn(second["decision_id"], second["action"])

    rows = _evidence_rows()
    assert [row["chain_index"] for row in rows] == [0, 1]
    assert all(row["payload_hash"] for row in rows)


def test_receipt_does_not_add_extra_conservation_increment() -> None:
    score = _score("EVID-RCP-CONSERVE-001")
    before_status = client.get("/api/conservation/status").json()
    before_verified = app.state.graph_store.count_verified_decisions("s2p")

    _learn(score["decision_id"], score["action"])

    after_status = client.get("/api/conservation/status").json()
    assert app.state.graph_store.count_verified_decisions("s2p") == before_verified + 1
    assert after_status["total_decisions"] == before_status["total_decisions"] + 1
    assert after_status["verified_count"] == before_status["verified_count"] + 1


def test_main_score_still_creates_decision_before_outcome() -> None:
    before_decisions = app.state.graph_store.count_decisions("s2p")

    score = _score("EVID-RCP-SCORE-001")

    assert score["decision_id"]
    assert app.state.graph_store.count_decisions("s2p") == before_decisions + 1
    assert _outcome_count() == 0


def test_outcome_route_appends_evidence_receipt() -> None:
    score = _score("EVID-RCP-OUTCOME-001")
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "evidence-receipt-test",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 200
    rows = _evidence_rows()
    assert len(rows) == 1
    assert rows[0]["actor"] == "evidence-receipt-test"
    assert rows[0]["source_route"] == "/api/s2p/outcome"
    assert rows[0]["payload_hash"]


def test_outcome_route_receipt_failure_blocks_outcome_write(monkeypatch) -> None:
    score = _score("EVID-RCP-NEO4J-BLOCK-001")
    store = app.state.graph_store
    calls: list[str] = []

    def append_fails(**_kwargs):
        calls.append("append")
        raise RuntimeError("append unavailable")

    def outbox_fails(**_kwargs):
        calls.append("outbox")
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(store, "append_evidence_receipt", append_fails)
    monkeypatch.setattr(store, "enqueue_to_outbox", outbox_fails)

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "receipt-block-test",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 503
    assert calls == ["append", "outbox"]
    assert _outcome_count() == 0


def test_outcome_route_outbox_fallback_precedes_outcome_write(monkeypatch) -> None:
    score = _score("EVID-RCP-NEO4J-OUTBOX-001")
    store = app.state.graph_store
    calls: list[str] = []
    original_enqueue = store.enqueue_to_outbox

    def append_fails(**_kwargs):
        calls.append("append")
        raise RuntimeError("append unavailable")

    def enqueue_spy(**kwargs):
        calls.append("outbox")
        return original_enqueue(**kwargs)

    monkeypatch.setattr(store, "append_evidence_receipt", append_fails)
    monkeypatch.setattr(store, "enqueue_to_outbox", enqueue_spy)

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "receipt-outbox-test",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 200
    assert calls[:2] == ["append", "outbox"]
    outbox = _outbox_rows()
    assert len(outbox) == 1
    assert json.loads(outbox[0]["payload_json"])["decision_id"] == score["decision_id"]
    assert _outcome_count() == 1


def test_factor_hash_is_deterministic_for_same_pre_outcome_payload() -> None:
    decision = {
        "decision_id": "EVID-HASH-1",
        "category": "price_variance",
        "recommended_action": "auto_approve",
        "confidence": 0.75,
        "factors": {name: index / 10 for index, name in enumerate(S2PDomainConfig.factors)},
    }

    assert s2p_router._receipt_factor_hash(decision) == s2p_router._receipt_factor_hash(dict(decision))
