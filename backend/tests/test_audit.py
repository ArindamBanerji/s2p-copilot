import asyncio
from pathlib import Path

import pytest

from app.framework import audit
from ci_platform.audit.evidence_ledger import LedgerEntry, OutcomeEntry


BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_audit_state():
    audit._LEDGER._entries.clear()
    audit._SITUATION_TYPES.clear()
    audit._ARCHIVED_EPOCHS.clear()
    yield
    audit._LEDGER._entries.clear()
    audit._SITUATION_TYPES.clear()
    audit._ARCHIVED_EPOCHS.clear()


def test_outcome_entry_is_separate_from_decision():
    decision = audit.record_decision(
        alert_id="PO-9001",
        situation_type="price_variance",
        action_taken="hold_for_review",
        factors=["amount_variance_ratio"],
        confidence=0.91,
    )

    outcome = audit.record_outcome(decision["decision_id"], "confirmed")
    entries = audit._LEDGER.entries()

    assert isinstance(entries[0], LedgerEntry)
    assert isinstance(entries[1], OutcomeEntry)
    assert outcome["type"] == "outcome"
    assert outcome["decision_id"] == decision["decision_id"]
    assert outcome["decision_entry_hash"] == entries[0].entry_hash


def test_outcome_does_not_mutate_decision_entry():
    decision = audit.record_decision(
        alert_id="PO-9002",
        situation_type="duplicate_risk",
        action_taken="flag_leakage",
        factors=["duplicate_score"],
        confidence=0.93,
    )
    sealed_entry = audit._LEDGER.entries()[0]
    original_hash = sealed_entry.entry_hash
    original_outcome = sealed_entry.outcome
    original_override = sealed_entry.analyst_override

    audit.record_outcome(decision["decision_id"], "overridden")

    assert sealed_entry.entry_hash == original_hash
    assert sealed_entry.outcome == original_outcome
    assert sealed_entry.analyst_override == original_override
    assert sealed_entry.is_valid() is True


def test_audit_chain_integrity():
    first = audit.record_decision(
        alert_id="PO-9003",
        situation_type="contract_gap",
        action_taken="escalate_to_buyer",
        factors=["supplier_exception_history"],
        confidence=0.88,
    )
    second = audit.record_decision(
        alert_id="PO-9004",
        situation_type="quantity_mismatch",
        action_taken="hold_for_review",
        factors=["match_status"],
        confidence=0.86,
    )
    audit.record_outcome(first["decision_id"], "confirmed")
    audit.record_outcome(second["decision_id"], "overridden")

    result = audit.verify_chain()

    assert result["verified"] is True
    assert result["chain_length"] == 4
    assert result["entries_checked"] == 4
    assert result["tamper_evidence"] == []


def test_empty_audit_chain_reports_no_tamper():
    result = audit.verify_chain()

    assert result["verified"] is True
    assert result["entries_checked"] == 0
    assert result["tamper_evidence"] == []


def test_audit_verify_chain_reports_action_tamper_evidence():
    audit.record_decision(
        alert_id="PO-TAMPER-1",
        situation_type="contract_gap",
        action_taken="escalate_to_buyer",
        factors=["supplier_exception_history"],
        confidence=0.88,
    )
    entry = audit._LEDGER._entries[0]
    entry.action = "pay_now"

    result = audit.verify_chain()

    assert result["verified"] is False
    assert result["broken_at_index"] == 0
    evidence = result["tamper_evidence"][0]
    assert evidence["index"] == 0
    assert evidence["type"] == "decision"
    assert evidence["decision_id"] == entry.decision_id
    assert evidence["detail"] == "entry_hash mismatch"
    assert evidence["expected_hash"] != evidence["actual_hash"]


def test_audit_verify_chain_reports_prev_hash_tamper_evidence():
    audit.record_decision(
        alert_id="PO-TAMPER-2",
        situation_type="contract_gap",
        action_taken="escalate_to_buyer",
        factors=["supplier_exception_history"],
        confidence=0.88,
    )
    audit.record_decision(
        alert_id="PO-TAMPER-3",
        situation_type="contract_gap",
        action_taken="escalate_to_buyer",
        factors=["supplier_exception_history"],
        confidence=0.88,
    )
    entry = audit._LEDGER._entries[1]
    entry.prev_hash = "broken"

    result = audit.verify_chain()

    assert result["verified"] is False
    assert any(item["detail"] == "prev_hash linkage mismatch" for item in result["tamper_evidence"])
    prev_evidence = next(item for item in result["tamper_evidence"] if item["detail"] == "prev_hash linkage mismatch")
    assert prev_evidence["index"] == 1
    assert prev_evidence["actual_prev_hash"] == "broken"


def test_audit_verify_chain_reports_outcome_tamper_evidence():
    decision = audit.record_decision(
        alert_id="PO-TAMPER-4",
        situation_type="duplicate_risk",
        action_taken="flag_leakage",
        factors=["duplicate_score"],
        confidence=0.93,
    )
    audit.record_outcome(decision["decision_id"], "confirmed")
    outcome = audit._LEDGER._entries[1]
    outcome.outcome = "overridden"

    result = audit.verify_chain()

    assert result["verified"] is False
    evidence = result["tamper_evidence"][0]
    assert evidence["index"] == 1
    assert evidence["type"] == "outcome"
    assert evidence["detail"] == "entry_hash mismatch"


def test_epoch_archive_creates_snapshot():
    decision = audit.record_decision(
        alert_id="PO-9005",
        situation_type="format_compliance",
        action_taken="refer_to_specialist",
        factors=["tax_regulatory_compliance"],
        confidence=0.89,
    )
    audit.record_outcome(decision["decision_id"], "confirmed")

    archive = audit.create_epoch_archive("operator_snapshot")
    archives = audit.get_epoch_archives()

    assert archive["epoch"] == 1
    assert archive["entry_count"] == 2
    assert archive["verified"] is True
    assert archives[0]["entry_count"] == 2
    assert [entry["type"] for entry in archives[0]["entries"] if "type" in entry] == [
        "outcome"
    ]


def test_async_concurrent_write_paths_preserve_chain():
    async def write_decision(index):
        return await audit.async_record_decision(
            alert_id=f"PO-ASYNC-{index}",
            situation_type="price_variance",
            action_taken="hold_for_review",
            factors=["amount_variance_ratio"],
            confidence=0.84,
        )

    async def write_all():
        return await asyncio.gather(*(write_decision(i) for i in range(5)))

    decisions = asyncio.run(write_all())

    assert len(decisions) == 5
    assert audit.verify_chain()["verified"] is True
    assert audit.verify_chain()["chain_length"] == 5


def test_no_soc_vocabulary_in_backported_framework_files():
    framework_dir = BACKEND_ROOT / "app" / "framework"
    checked_files = [
        framework_dir / "audit.py",
        framework_dir / "composite_gate.py",
        framework_dir / "intervention_controls.py",
    ]
    forbidden = [
        "credential_access",
        "malware_execution",
        "data_exfiltration",
        "lateral_movement",
        "privilege_escalation",
        "suppress",
        "refer_to_analyst",
    ]

    matches = []
    for path in checked_files:
        source = path.read_text(encoding="utf-8")
        matches.extend(f"{path}:{term}" for term in forbidden if term in source)

    assert matches == []
