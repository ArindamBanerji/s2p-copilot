from __future__ import annotations

import json
from enum import Enum

from app.models.intents import (
    ClassifiedIntent,
    INTENT_METADATA,
    IntentCategory,
    IntentType,
)
from app.services.intent_classifier import classify_intent


def test_intent_type_has_expected_values():
    expected = {
        "triage_price",
        "triage_quantity",
        "triage_duplicate",
        "triage_contract",
        "triage_format",
        "auto_approve",
        "hold_review",
        "escalate_buyer",
        "escalate_manager",
        "refer_specialist",
        "query_invoice",
        "query_supplier",
        "query_compliance",
        "query_conservation",
        "report_financial",
        "report_audit",
        "batch_process",
    }

    assert len(IntentType) >= 15
    assert {intent.value for intent in IntentType} == expected
    assert issubclass(IntentType, str)
    assert issubclass(IntentType, Enum)


def test_all_intents_have_metadata():
    assert set(INTENT_METADATA) == set(IntentType)
    for metadata in INTENT_METADATA.values():
        assert isinstance(metadata["category"], IntentCategory)
        assert metadata["description"]
        assert "default_action" in metadata
        assert metadata["priority"]


def test_classified_intent_json_serializes_enum_values():
    result = ClassifiedIntent(
        intent=IntentType.triage_price,
        confidence=0.9,
        category=IntentCategory.triage,
        description="test",
        default_action="hold_for_review",
        priority="high",
    )

    dump_json = getattr(result, "model_dump_json", None)
    payload = json.loads(dump_json() if callable(dump_json) else result.json())
    assert payload["intent"] == "triage_price"
    assert payload["category"] == "triage"


def test_category_rules_map_to_triage_intents():
    assert classify_intent({"category": "price_variance"}).intent is IntentType.triage_price
    assert classify_intent({"category": "quantity_mismatch"}).intent is IntentType.triage_quantity
    assert classify_intent({"category": "duplicate_risk"}).intent is IntentType.triage_duplicate
    assert classify_intent({"category": "contract_gap"}).intent is IntentType.triage_contract
    assert classify_intent({"category": "format_compliance"}).intent is IntentType.triage_format


def test_query_rules_map_to_domain_queries_and_reports():
    assert classify_intent({}, query="show supplier vendor history").intent is IntentType.query_supplier
    assert classify_intent({}, query="SOX compliance status").intent is IntentType.query_compliance
    assert classify_intent({}, query="is conservation green").intent is IntentType.query_conservation
    assert classify_intent({}, query="financial impact report").intent is IntentType.report_financial
    assert classify_intent({}, query="download audit trail export").intent is IntentType.report_audit
    assert classify_intent({}, query="bulk batch all invoices").intent is IntentType.batch_process


def test_action_rules_map_to_action_intents():
    assert classify_intent({"action_hint": "auto_approve"}).intent is IntentType.auto_approve
    assert classify_intent({"action_hint": "hold for review"}).intent is IntentType.hold_review
    assert classify_intent({"action_hint": "send to buyer"}).intent is IntentType.escalate_buyer
    assert classify_intent({"action_hint": "manager approval"}).intent is IntentType.escalate_manager
    assert classify_intent({"action_hint": "refer specialist"}).intent is IntentType.refer_specialist


def test_unknown_category_defaults_to_hold_review_low_confidence():
    result = classify_intent({"category": "unknown"})

    assert result.intent is IntentType.hold_review
    assert result.confidence < 0.6
    assert result.intent.value == "hold_review"
