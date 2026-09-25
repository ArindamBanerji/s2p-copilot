"""End-to-end S2P scoring and learning checks without receipt no-op patches."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.receipt_store import get_receipt_store, reset_receipt_store


client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_receipts() -> None:
    reset_receipt_store()


def _score(event_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/s2p/score",
        json={
            "event_id": event_id,
            "category": "price_variance",
            "amount": 5000.0,
            "supplier_id": "SUP-UNPATCHED",
            "match_status": 0.92,
            "amount_variance_ratio": 0.08,
            "duplicate_score": 0.04,
            "supplier_exception_history": 0.05,
            "payment_terms_impact": 0.48,
            "commodity_index_correlation": 0.76,
            "tax_regulatory_compliance": 0.90,
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _learn(score: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _evidence_rows() -> list[dict[str, Any]]:
    rows = []
    for row in app.state.graph_store._evidence_receipts.values():
        copied = dict(row)
        copied["canonical_payload_json"] = json.dumps(copied["canonical_payload"])
        rows.append(copied)
    return sorted(rows, key=lambda row: int(row["chain_index"]))


def test_score_outcome_verify_unpatched() -> None:
    score = _score("UNPATCHED-FLOW-001")
    learned = _learn(score)

    assert learned["decision_id"] == score["decision_id"]
    assert learned["learning_applied"] is True
    receipts = get_receipt_store().get_for_decision(score["decision_id"])
    assert receipts
    receipt = receipts[-1]
    assert {
        "receipt_id",
        "decision_id",
        "scored_action",
        "actual_action",
        "is_correct",
        "confidence",
        "category",
        "conservation_state_before",
        "conservation_state_after",
        "receipt_hash",
    } <= set(receipt)
    assert receipt["decision_id"] == score["decision_id"]
    assert client.get("/api/s2p/evidence/chain-integrity").status_code == 200


def test_outcome_ordering_unpatched() -> None:
    score = _score("UNPATCHED-FLOW-002")
    _learn(score)

    rows = _evidence_rows()
    assert len(rows) == 2
    payloads = [json.loads(row["canonical_payload_json"]) for row in rows]
    assert payloads[0]["receipt_type"] == "pre_outcome_context"
    assert payloads[1]["receipt_type"] == "post_outcome_verification"
    assert rows[0]["chain_index"] < rows[1]["chain_index"]
    assert all(row["payload_hash"] for row in rows)


def test_receipt_persistence_unpatched() -> None:
    score = _score("UNPATCHED-FLOW-003")
    _learn(score)

    store = app.state.graph_store
    evidence = list(store._evidence_receipts.values())
    assert any(row["decision_id"] == score["decision_id"] for row in evidence)
    persisted = get_receipt_store().get_for_decision(score["decision_id"])
    assert len(persisted) == 1
    assert get_receipt_store().verify_chain()["verified"] is True


def test_conservation_receipt_real_unpatched() -> None:
    score = _score("UNPATCHED-FLOW-004")
    before = client.get("/api/conservation/status")
    assert before.status_code == 200, before.text
    _learn(score)
    after = client.get("/api/conservation/status")
    assert after.status_code == 200, after.text
    payload = cast(dict[str, Any], after.json())
    assert payload["domain"] == "s2p"
    assert isinstance(payload["total_decisions"], int)
    assert isinstance(payload["verified_count"], int)
    assert payload["verified_count"] >= 1
    receipt = get_receipt_store().get_for_decision(score["decision_id"])[0]
    assert receipt["conservation_state_before"] is not None
    assert receipt["conservation_state_after"] is not None


def test_503_on_both_paths_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    score = _score("UNPATCHED-FLOW-005")
    store = app.state.graph_store

    def append_fails(**_: Any) -> None:
        raise RuntimeError("evidence store unavailable")

    def outbox_fails(**_: Any) -> None:
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
    assert get_receipt_store().get_for_decision(score["decision_id"]) == []
