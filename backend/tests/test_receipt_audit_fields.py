from __future__ import annotations

import dataclasses
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.models.outcome_receipt import OutcomeReceipt
from app.routers import s2p as s2p_router
from app.services.receipt_store import get_receipt_store, reset_receipt_store
from app.services.s2p_evolver import reset_s2p_evolver
from app.services.supplier_profile_accumulator import accumulator as supplier_profile_accumulator


client = TestClient(app)


def _reset_app_state() -> None:
    reset_receipt_store()
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    s2p_router._clear_score_conservation_status_cache()
    reset_s2p_evolver()
    supplier_profile_accumulator.reset()


def setup_function() -> None:
    _reset_app_state()


def teardown_function() -> None:
    _reset_app_state()


def _receipt(**overrides) -> OutcomeReceipt:
    payload = {
        "receipt_id": "AUDIT-R-1",
        "invoice_id": "S2P-INV-AUDIT-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "scored_action": "auto_approve",
        "confidence": 0.91,
        "factor_vector": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        "category": "price_variance",
        "human_action": "auto_approve",
    }
    payload.update(overrides)
    return OutcomeReceipt(**payload)


def _score(event_id: str = "S2P-INV-0001") -> dict:
    response = client.post(
        "/api/s2p/score",
        json={
            "event_id": event_id,
            "category": "price_variance",
            "amount": 22426.73,
            "supplier_id": "SUP-001",
            "match_status": 0.698,
            "amount_variance_ratio": 0.142,
            "duplicate_score": 0.065,
            "supplier_exception_history": 0.164,
            "payment_terms_impact": 0.859,
            "commodity_index_correlation": 0.194,
            "tax_regulatory_compliance": 0.586,
        },
    )
    assert response.status_code == 200
    return response.json()


def _learn(
    decision_id: str,
    actual_action: str,
    outcome: str = "confirmed",
    reason_code: str | None = None,
) -> dict:
    payload = {
        "decision_id": decision_id,
        "actual_action": actual_action,
        "outcome": outcome,
    }
    if reason_code is not None:
        payload["reason_code"] = reason_code
    response = client.post(
        "/api/learn",
        json=payload,
    )
    assert response.status_code == 200
    return response.json()


def test_receipt_dataclass_has_pd_audit_fields() -> None:
    fields = {field.name for field in dataclasses.fields(OutcomeReceipt)}

    assert {"amount_recovered", "supplier_name", "invoice_number", "po_number"} <= fields


def test_pd_audit_fields_default_to_none_and_minimal_constructor_still_works() -> None:
    receipt = _receipt()

    assert receipt.amount_recovered is None
    assert receipt.supplier_name is None
    assert receipt.invoice_number is None
    assert receipt.po_number is None


def test_receipt_stores_explicit_pd_audit_fields() -> None:
    receipt = _receipt(
        amount_recovered="123.45",
        supplier_name="Aster Industrial Chemicals",
        invoice_number="S2P-INV-0001",
        po_number="PO-20260001",
    )
    payload = receipt.to_dict()

    assert receipt.amount_recovered == 123.45
    assert payload["amount_recovered"] == 123.45
    assert payload["supplier_name"] == "Aster Industrial Chemicals"
    assert payload["invoice_number"] == "S2P-INV-0001"
    assert payload["po_number"] == "PO-20260001"


def test_amount_recovered_hash_basis_for_new_receipts() -> None:
    assert _receipt(amount_recovered=10.0).receipt_hash != _receipt(amount_recovered=11.0).receipt_hash
    assert _receipt(supplier_name="Supplier A").receipt_hash != _receipt(supplier_name="Supplier B").receipt_hash
    assert _receipt().receipt_hash == _receipt().receipt_hash


def test_correct_production_learn_receipt_populates_pd_audit_fields() -> None:
    score = _score("S2P-INV-0001")

    _learn(score["decision_id"], score["action"])

    receipts = get_receipt_store().get_for_invoice("S2P-INV-0001")
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["amount_recovered"] == 22426.73
    assert receipt["supplier_name"] == "Aster Industrial Chemicals"
    assert receipt["invoice_number"] == "S2P-INV-0001"
    assert receipt["po_number"] == "PO-20260001"
    assert receipt["receipt_hash"]
    assert "previous_hash" in receipt
    assert get_receipt_store().verify_chain()["verified"] is True


def test_incorrect_production_learn_receipt_has_zero_amount_recovered() -> None:
    score = _score("S2P-INV-0001")
    wrong_action = next(action for action in S2PDomainConfig.actions if action != score["action"])

    _learn(score["decision_id"], wrong_action, "override", reason_code="wrong_action")

    receipt = get_receipt_store().get_for_invoice("S2P-INV-0001")[0]
    assert receipt["is_correct"] is False
    assert receipt["amount_recovered"] == 0.0
    assert receipt["supplier_name"] == "Aster Industrial Chemicals"
    assert receipt["invoice_number"] == "S2P-INV-0001"
    assert receipt["po_number"] == "PO-20260001"


def test_outcome_route_receipt_populates_fields_from_decision_metadata() -> None:
    score = _score("S2P-INV-0001")
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "receipt-audit-test",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 200
    receipt = get_receipt_store().get_for_decision(score["decision_id"])[0]
    assert receipt["amount_recovered"] == 22426.73
    assert receipt["supplier_name"] == "Aster Industrial Chemicals"
    assert receipt["invoice_number"] == "S2P-INV-0001"
    assert receipt["po_number"] == "PO-20260001"


def test_missing_invoice_metadata_does_not_invent_string_fields_from_decision_id() -> None:
    s2p_router._record_outcome_receipt(
        decision={"decision_id": "DECISION-ONLY-1", "recommended_action": "auto_approve"},
        payload={"decision_id": "DECISION-ONLY-1", "reward": 1.0},
        actual_action="auto_approve",
        reason_code=None,
        conservation_before={"state": "GREEN", "verified_count": 0},
        conservation_after={"state": "GREEN", "verified_count": 1},
        context={},
    )

    receipt = get_receipt_store().get_for_decision("DECISION-ONLY-1")[0]
    assert receipt["invoice_number"] is None
    assert receipt["supplier_name"] is None
    assert receipt["po_number"] is None


def test_supplier_id_only_metadata_does_not_become_supplier_name() -> None:
    s2p_router._record_outcome_receipt(
        decision={
            "decision_id": "SUPPLIER-ID-ONLY-1",
            "recommended_action": "auto_approve",
            "metadata": {
                "invoice_id": "INV-SUPPLIER-ID-ONLY-1",
                "supplier_id": "SUP-NO-NAME",
            },
        },
        payload={"decision_id": "SUPPLIER-ID-ONLY-1", "reward": 1.0},
        actual_action="auto_approve",
        reason_code=None,
        conservation_before={"state": "GREEN", "verified_count": 0},
        conservation_after={"state": "GREEN", "verified_count": 1},
        context={},
    )

    receipt = get_receipt_store().get_for_invoice("INV-SUPPLIER-ID-ONLY-1")[0]
    assert receipt["supplier_name"] is None
    assert receipt["invoice_number"] == "INV-SUPPLIER-ID-ONLY-1"
    assert receipt["po_number"] is None


def test_source_invoice_id_populates_invoice_number_when_distinct_from_decision_id() -> None:
    s2p_router._record_outcome_receipt(
        decision={
            "decision_id": "DECISION-WITH-SOURCE-1",
            "recommended_action": "auto_approve",
            "metadata": {"source_invoice_id": "INV-SOURCE-1"},
        },
        payload={"decision_id": "DECISION-WITH-SOURCE-1", "reward": 1.0},
        actual_action="auto_approve",
        reason_code=None,
        conservation_before={"state": "GREEN", "verified_count": 0},
        conservation_after={"state": "GREEN", "verified_count": 1},
        context={},
    )

    receipt = get_receipt_store().get_for_invoice("INV-SOURCE-1")[0]
    assert receipt["invoice_number"] == "INV-SOURCE-1"


def test_correct_receipt_without_amount_records_zero_recovered_due_missing_amount_source() -> None:
    s2p_router._record_outcome_receipt(
        decision={
            "decision_id": "NO-AMOUNT-1",
            "recommended_action": "auto_approve",
            "metadata": {"invoice_id": "INV-NO-AMOUNT-1"},
        },
        payload={"decision_id": "NO-AMOUNT-1", "reward": 1.0},
        actual_action="auto_approve",
        reason_code=None,
        conservation_before={"state": "GREEN", "verified_count": 0},
        conservation_after={"state": "GREEN", "verified_count": 1},
        context={},
    )

    receipt = get_receipt_store().get_for_invoice("INV-NO-AMOUNT-1")[0]
    assert receipt["is_correct"] is True
    assert receipt["amount_recovered"] == 0.0
