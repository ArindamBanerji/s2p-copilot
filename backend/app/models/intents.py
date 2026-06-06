"""Typed S2P Control Tower intent taxonomy."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class IntentType(str, Enum):
    triage_price = "triage_price"
    triage_quantity = "triage_quantity"
    triage_duplicate = "triage_duplicate"
    triage_contract = "triage_contract"
    triage_format = "triage_format"
    auto_approve = "auto_approve"
    hold_review = "hold_review"
    escalate_buyer = "escalate_buyer"
    escalate_manager = "escalate_manager"
    refer_specialist = "refer_specialist"
    query_invoice = "query_invoice"
    query_supplier = "query_supplier"
    query_compliance = "query_compliance"
    query_conservation = "query_conservation"
    report_financial = "report_financial"
    report_audit = "report_audit"
    batch_process = "batch_process"


class IntentCategory(str, Enum):
    triage = "triage"
    action = "action"
    query = "query"
    operational = "operational"


INTENT_METADATA: dict[IntentType, dict[str, Any]] = {
    IntentType.triage_price: {
        "category": IntentCategory.triage,
        "description": "Invoice price or commodity variance needs triage.",
        "default_action": "hold_for_review",
        "priority": "high",
    },
    IntentType.triage_quantity: {
        "category": IntentCategory.triage,
        "description": "Invoice quantity does not align with purchase order or receipt.",
        "default_action": "hold_for_review",
        "priority": "high",
    },
    IntentType.triage_duplicate: {
        "category": IntentCategory.triage,
        "description": "Potential duplicate invoice or repeated supplier submission.",
        "default_action": "hold_for_review",
        "priority": "critical",
    },
    IntentType.triage_contract: {
        "category": IntentCategory.triage,
        "description": "Contract coverage, pricing, or policy evidence needs review.",
        "default_action": "escalate_to_buyer",
        "priority": "high",
    },
    IntentType.triage_format: {
        "category": IntentCategory.triage,
        "description": "Invoice format, required field, or completeness issue.",
        "default_action": "hold_for_review",
        "priority": "medium",
    },
    IntentType.auto_approve: {
        "category": IntentCategory.action,
        "description": "Invoice appears eligible for automated approval.",
        "default_action": "auto_approve",
        "priority": "low",
    },
    IntentType.hold_review: {
        "category": IntentCategory.action,
        "description": "Invoice should be held for analyst review.",
        "default_action": "hold_for_review",
        "priority": "medium",
    },
    IntentType.escalate_buyer: {
        "category": IntentCategory.action,
        "description": "Buyer intervention is required.",
        "default_action": "escalate_to_buyer",
        "priority": "high",
    },
    IntentType.escalate_manager: {
        "category": IntentCategory.action,
        "description": "Manager approval or exception handling is required.",
        "default_action": "refer_to_specialist",
        "priority": "high",
    },
    IntentType.refer_specialist: {
        "category": IntentCategory.action,
        "description": "Specialist review is required for a nonstandard exception.",
        "default_action": "refer_to_specialist",
        "priority": "high",
    },
    IntentType.query_invoice: {
        "category": IntentCategory.query,
        "description": "User is asking for invoice status, location, or tracking details.",
        "default_action": None,
        "priority": "low",
    },
    IntentType.query_supplier: {
        "category": IntentCategory.query,
        "description": "User is asking about supplier or vendor performance/context.",
        "default_action": None,
        "priority": "low",
    },
    IntentType.query_compliance: {
        "category": IntentCategory.query,
        "description": "User is asking about compliance, SOX, tax, or policy context.",
        "default_action": None,
        "priority": "medium",
    },
    IntentType.query_conservation: {
        "category": IntentCategory.query,
        "description": "User is asking about conservation state, safety, or green status.",
        "default_action": None,
        "priority": "medium",
    },
    IntentType.report_financial: {
        "category": IntentCategory.operational,
        "description": "User wants financial impact, leakage, or savings reporting.",
        "default_action": None,
        "priority": "medium",
    },
    IntentType.report_audit: {
        "category": IntentCategory.operational,
        "description": "User wants an audit trail, export, or downloadable evidence report.",
        "default_action": None,
        "priority": "medium",
    },
    IntentType.batch_process: {
        "category": IntentCategory.operational,
        "description": "User wants a batch, bulk, or all-invoices operation.",
        "default_action": "hold_for_review",
        "priority": "medium",
    },
}


class ClassifiedIntent(BaseModel):
    intent: IntentType
    confidence: float
    category: IntentCategory
    description: str
    default_action: str | None = None
    priority: str | None = None
