from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.models.outcome_receipt import OutcomeReceipt
from app.routers import s2p as s2p_router
from app.services.receipt_store import get_receipt_store, reset_receipt_store
from app.services.s2p_evolver import reset_s2p_evolver
from app.services.supplier_profile_accumulator import accumulator as supplier_profile_accumulator


client = TestClient(app)
VALID_SCORE_REQUEST = {
    "event_id": "EVID-RCPT-001",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-RCPT",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def make_receipt(**overrides) -> OutcomeReceipt:
    payload = {
        "receipt_id": "R-1",
        "decision_id": "D-1",
        "invoice_id": "INV-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "scored_action": "hold_for_review",
        "recommended_action": "hold_for_review",
        "confidence": 0.81234567,
        "factors": {
            name: value
            for name, value in zip(
                S2PDomainConfig.factors,
                [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
            )
        },
        "factor_vector": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        "category": "price_variance",
        "human_action": "hold_for_review",
        "actual_action": "hold_for_review",
        "is_correct": True,
        "conservation_status": "RED",
        "amount": 1000.0,
        "amount_at_risk": 250.0,
        "reward": 1.0,
        "centroid_updated": True,
        "conservation_state_before": "RED",
        "conservation_state_after": "RED",
        "verified_count_before": 0,
        "verified_count_after": 1,
    }
    payload.update(overrides)
    return OutcomeReceipt(**payload)


def setup_function():
    reset_receipt_store()
    reset_sdk_scorer()


def teardown_function():
    reset_receipt_store()
    reset_sdk_scorer()


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    reset_s2p_evolver()
    supplier_profile_accumulator.reset()


def score_for_receipt(**overrides) -> dict:
    response = client.post("/api/s2p/score", json={**VALID_SCORE_REQUEST, **overrides})
    assert response.status_code == 200
    return response.json()


def learn_for_receipt(score: dict, **overrides) -> dict:
    payload = {
        "decision_id": score["decision_id"],
        "actual_action": score["action"],
        "outcome": "confirmed",
        "context": {"analyst": "receipt-test"},
    }
    payload.update(overrides)
    response = client.post("/api/learn", json=payload)
    assert response.status_code == 200
    return response.json()


def test_receipt_has_hash():
    receipt = make_receipt()

    assert len(receipt.receipt_hash) == 16


def test_receipt_hash_deterministic():
    assert make_receipt().receipt_hash == make_receipt().receipt_hash


def test_receipt_hash_changes_with_content():
    assert make_receipt(reward=1.0).receipt_hash != make_receipt(reward=-1.0).receipt_hash


def test_to_dict_has_all_fields():
    payload = make_receipt().to_dict()

    assert {
        "receipt_id",
        "decision_id",
        "invoice_id",
        "timestamp",
        "scored_action",
        "recommended_action",
        "actual_action",
        "is_correct",
        "confidence",
        "factors",
        "factor_vector",
        "category",
        "human_action",
        "conservation_status",
        "amount",
        "amount_at_risk",
        "override_reason",
        "reward",
        "centroid_updated",
        "conservation_state_before",
        "conservation_state_after",
        "verified_count_before",
        "verified_count_after",
        "previous_hash",
        "receipt_hash",
    } <= set(payload)


def test_override_reason_captured():
    payload = make_receipt(
        human_action="escalate_to_buyer",
        override_reason="supplier_risk",
    ).to_dict()

    assert payload["override_reason"] == "supplier_risk"


def test_empty_store():
    store = get_receipt_store()

    assert store.count == 0
    assert store.last_hash == ""
    assert store.stats["total_receipts"] == 0


def test_add_and_count():
    store = get_receipt_store()

    store.add(make_receipt())

    assert store.count == 1


def test_chain_integrity_valid():
    store = get_receipt_store()
    first = store.add(make_receipt(receipt_id="R-1"))
    second = store.add(make_receipt(receipt_id="R-2", invoice_id="INV-2"))

    assert second.previous_receipt_hash == first.receipt_hash
    assert store.verify_chain()["verified"] is True


def test_chain_detects_break():
    store = get_receipt_store()
    receipt = store.add(make_receipt())
    receipt.reward = -99.0

    result = store.verify_chain()

    assert result["verified"] is False
    assert result["broken_at_index"] == 0


def test_get_for_invoice():
    store = get_receipt_store()
    store.add(make_receipt(invoice_id="INV-1"))
    store.add(make_receipt(receipt_id="R-2", invoice_id="INV-2"))

    receipts = store.get_for_invoice("INV-1")

    assert len(receipts) == 1
    assert receipts[0]["invoice_id"] == "INV-1"


def test_override_stats():
    store = get_receipt_store()
    store.add(make_receipt(receipt_id="R-1"))
    store.add(
        make_receipt(
            receipt_id="R-2",
            invoice_id="INV-2",
            human_action="escalate_to_buyer",
            override_reason="supplier_risk",
        )
    )

    stats = store.stats

    assert stats["confirms"] == 1
    assert stats["overrides"] == 1
    assert stats["override_rate"] == 0.5


def test_clear():
    store = get_receipt_store()
    store.add(make_receipt())

    store.clear()

    assert store.count == 0
    assert store.get_for_invoice("INV-1") == []
    assert store.get_for_decision("D-1") == []


def test_store_get_for_decision():
    store = get_receipt_store()
    store.add(make_receipt(decision_id="DECISION-RCPT-1"))

    receipts = store.get_for_decision("DECISION-RCPT-1")

    assert len(receipts) == 1
    assert receipts[0]["decision_id"] == "DECISION-RCPT-1"


def test_receipts_endpoint_200():
    get_receipt_store().add(make_receipt())

    response = client.get("/api/s2p/evidence/receipts")

    assert response.status_code == 200
    assert response.json()["stats"]["total_receipts"] == 1


def test_chain_integrity_endpoint_200():
    response = client.get("/api/s2p/evidence/chain-integrity")

    assert response.status_code == 200
    assert response.json()["verified"] is True


def test_audit_pack_endpoint_200():
    get_receipt_store().add(make_receipt())

    response = client.get("/api/s2p/evidence/audit-pack")

    assert response.status_code == 200
    payload = response.json()
    assert payload["receipt_count"] == 1
    assert payload["chain_integrity"]["verified"] is True
    assert "override_distribution" in payload
    assert "conservation_state" in payload


def test_invoice_receipts_404_unknown():
    response = client.get("/api/s2p/evidence/receipts/UNKNOWN")

    assert response.status_code == 404


def test_decision_receipts_endpoint_200():
    get_receipt_store().add(make_receipt(decision_id="DECISION-RCPT-1"))

    response = client.get("/api/s2p/evidence/receipts/decision/DECISION-RCPT-1")

    assert response.status_code == 200
    data = response.json()
    assert data["decision_id"] == "DECISION-RCPT-1"
    assert data["receipts"][0]["decision_id"] == "DECISION-RCPT-1"


def test_decision_receipts_404_unknown():
    response = client.get("/api/s2p/evidence/receipts/decision/UNKNOWN")

    assert response.status_code == 404


def test_receipt_routes_mounted():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/evidence/receipts" in paths
    assert "/api/s2p/evidence/receipts/{invoice_id}" in paths
    assert "/api/s2p/evidence/receipts/decision/{decision_id}" in paths
    assert "/api/s2p/evidence/chain-integrity" in paths
    assert "/api/s2p/evidence/audit-pack" in paths


def test_learn_creates_receipt():
    score = score_for_receipt()

    learn_for_receipt(score)

    receipts = get_receipt_store().get_for_invoice(VALID_SCORE_REQUEST["event_id"])
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["invoice_id"] == VALID_SCORE_REQUEST["event_id"]
    assert receipt["decision_id"] == score["decision_id"]
    assert receipt["scored_action"] == score["action"]
    assert receipt["recommended_action"] == score["action"]
    assert receipt["human_action"] == score["action"]
    assert receipt["actual_action"] == score["action"]
    assert receipt["is_correct"] is True
    assert set(receipt["factors"]) == set(S2PDomainConfig.factors)
    assert receipt["conservation_status"]
    assert len(receipt["factor_vector"]) == S2PDomainConfig.n_factors
    assert receipt["receipt_hash"]


def test_chain_grows_with_multiple_learns(monkeypatch):
    first = score_for_receipt(event_id="EVID-RCPT-001")
    second = score_for_receipt(event_id="EVID-RCPT-002")
    invoice_by_decision = {
        first["decision_id"]: "EVID-RCPT-001",
        second["decision_id"]: "EVID-RCPT-002",
    }
    snapshots = iter([
        {"state": "GREEN", "verified_count": 0},
        {"state": "GREEN", "verified_count": 1},
        {"state": "GREEN", "verified_count": 1},
        {"state": "GREEN", "verified_count": 2},
    ])

    def recorded_learn(_scorer, decision_id, *_args, **_kwargs):
        return {
            "decision_id": decision_id,
            "invoice_id": invoice_by_decision[decision_id],
            "outcome": "confirmed",
            "reward": 0.8,
        }

    monkeypatch.setattr(s2p_router, "_receipt_conservation_snapshot", lambda _request: next(snapshots))
    monkeypatch.setattr(s2p_router, "_learn_with_scorer", recorded_learn)

    learn_for_receipt(first)
    learn_for_receipt(second)

    chain = get_receipt_store().get_chain()

    assert len(chain) == 2
    assert chain[1]["previous_hash"] == chain[0]["receipt_hash"]


def test_learn_receipt_contains_conservation_before_after(monkeypatch):
    score = score_for_receipt()
    states = iter(["RED", "GREEN"])
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: next(states))

    learn_for_receipt(score)

    receipt = get_receipt_store().get_for_invoice(VALID_SCORE_REQUEST["event_id"])[0]
    assert receipt["conservation_state_before"] == "RED"
    assert receipt["conservation_state_after"] == "GREEN"
    assert receipt["verified_count_after"] >= receipt["verified_count_before"]


def test_override_learn_receipt_has_override_reason():
    score = score_for_receipt()
    override_action = next(action for action in S2PDomainConfig.actions if action != score["action"])

    learn_for_receipt(
        score,
        actual_action=override_action,
        outcome="override",
        reason_code="wrong_action",
    )

    receipt = get_receipt_store().get_for_invoice(VALID_SCORE_REQUEST["event_id"])[0]
    assert receipt["human_action"] == override_action
    assert receipt["actual_action"] == override_action
    assert receipt["is_correct"] is False
    assert receipt["override_reason"] == "wrong_action"


def test_outcome_route_creates_receipt():
    score = score_for_receipt(event_id="EVID-RCPT-OUTCOME")

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "receipt-test",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
            "amount": 5000.0,
            "at_risk": 750.0,
        },
    )

    assert response.status_code == 200
    receipts = get_receipt_store().get_for_decision(score["decision_id"])
    assert len(receipts) == 1
    assert receipts[0]["amount"] == 5000.0
    assert receipts[0]["amount_at_risk"] == 750.0


def test_paused_learning_without_variant_still_creates_receipt(monkeypatch):
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "RED")
    score = score_for_receipt()

    payload = learn_for_receipt(score)

    assert payload["evolution_recorded"] is False
    receipts = get_receipt_store().get_for_invoice(VALID_SCORE_REQUEST["event_id"])
    assert len(receipts) == 1
    assert receipts[0]["conservation_state_before"] == "RED"
    assert receipts[0]["conservation_state_after"] == "RED"


def test_receipt_not_created_when_scorer_pauses_before_outcome_write(monkeypatch):
    score = score_for_receipt()

    def paused_learn(*_args, **_kwargs):
        return {
            "status": "paused",
            "reason": "conservation_red",
            "verified_count": 0,
            "correct_count": 0,
        }

    monkeypatch.setattr(s2p_router, "_learn_with_scorer", paused_learn)

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    assert get_receipt_store().count == 0
    assert get_receipt_store().verify_chain()["verified"] is True


def test_receipt_created_when_learn_result_proves_outcome_recorded(monkeypatch):
    score = score_for_receipt()
    snapshots = iter([
        {"state": "GREEN", "verified_count": 0},
        {"state": "GREEN", "verified_count": 1},
    ])

    def recorded_learn(*_args, **_kwargs):
        return {
            "decision_id": score["decision_id"],
            "invoice_id": VALID_SCORE_REQUEST["event_id"],
            "outcome": "confirmed",
            "reward": 0.8,
        }

    monkeypatch.setattr(s2p_router, "_receipt_conservation_snapshot", lambda _request: next(snapshots))
    monkeypatch.setattr(s2p_router, "_learn_with_scorer", recorded_learn)

    payload = learn_for_receipt(score)

    assert payload["reward"] == 0.8
    assert get_receipt_store().count == 1


def test_paused_learning_without_variant_still_does_not_422(monkeypatch):
    score = score_for_receipt()
    monkeypatch.setattr(
        s2p_router,
        "_learn_with_scorer",
        lambda *_args, **_kwargs: {"status": "paused", "reason": "conservation_red"},
    )

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 200
    assert response.status_code != 422
    assert get_receipt_store().count == 0


def test_outcome_recorded_helper_rejects_paused_payload():
    assert s2p_router._outcome_recorded_for_receipt(
        {"status": "paused", "reason": "conservation_red"},
        1,
        1,
    ) is False


def test_outcome_recorded_helper_accepts_verified_count_delta():
    assert s2p_router._outcome_recorded_for_receipt(
        {"decision_id": "D-1", "outcome": "confirmed"},
        1,
        2,
    ) is True


def test_receipt_chain_valid_after_multiple_learns():
    first = score_for_receipt(event_id="EVID-RCPT-001")
    learn_for_receipt(first)
    second = score_for_receipt(event_id="EVID-RCPT-002")
    learn_for_receipt(second)

    assert get_receipt_store().verify_chain()["verified"] is True
