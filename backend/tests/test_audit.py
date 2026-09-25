"""Audit contracts exercised against real GraphStore adapters, not a shadow ledger."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.framework import audit
from app.routers.s2p_audit_export import router
from ci_platform.audit.evidence_ledger import LedgerEntry, OutcomeEntry
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.graph.sqlite_store import SQLiteGraphStore

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True, params=["memory", "sqlite"])
def audit_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[GraphStore]:
    previous = audit._GRAPH_STORE
    store = (InMemoryGraphStore(domain="s2p") if request.param == "memory"
             else SQLiteGraphStore(tmp_path / "audit.db", domain="s2p"))
    audit.configure_graph_store(store)
    try:
        yield store
    finally:
        audit.configure_graph_store(previous)
        store.close()


def record(index: int = 0) -> dict[str, Any]:
    return audit.record_decision(
        alert_id=f"PO-AUDIT-{index}", situation_type="price_variance",
        action_taken="hold_for_review", factors=["amount_variance_ratio"], confidence=0.91,
    )


def stored_entries(store: GraphStore) -> list[dict[str, Any]]:
    return sorted(
        [row for row in store.list_ledgers("s2p") if row["key"].startswith("s2p-audit-entry:")],
        key=lambda row: row["chain_index"],
    )


def persist(store: GraphStore, row: dict[str, Any]) -> None:
    payload = dict(row)
    key = payload.pop("key")
    store.save_ledger("s2p", key, payload)


def test_record_decision_stores_entry(audit_store: GraphStore) -> None:
    decision = record()
    entries = stored_entries(audit_store)
    assert len(entries) == 1
    assert entries[0]["decision_id"] == decision["decision_id"]
    assert entries[0]["entry_hash"] == decision["hash"]
    assert audit_store.get_decision(decision["decision_id"], "s2p") is not None


def test_record_outcome_stores_entry(audit_store: GraphStore) -> None:
    decision = record()
    outcome = audit.record_outcome(decision["decision_id"], "confirmed")
    assert outcome is not None
    entries = stored_entries(audit_store)
    assert [entry["type"] for entry in entries] == ["decision", "outcome"]
    assert outcome["decision_entry_hash"] == entries[0]["entry_hash"]
    assert entries[1]["outcome"] == "confirmed"
    assert entries[1]["prev_hash"] == entries[0]["entry_hash"]


def test_outcome_does_not_mutate_decision_entry(audit_store: GraphStore) -> None:
    decision = record()
    original = stored_entries(audit_store)[0]
    audit.record_outcome(decision["decision_id"], "overridden")
    assert stored_entries(audit_store)[0] == original
    assert audit.verify_chain()["verified"] is True


def test_verify_chain_clean() -> None:
    for index in range(5):
        decision = record(index)
        audit.record_outcome(decision["decision_id"], "confirmed")
    result = audit.verify_chain()
    assert result["verified"] is True
    assert result["chain_length"] == result["entries_checked"] == 10
    assert result["tamper_evidence"] == []


def test_verify_chain_tampered_hash(audit_store: GraphStore) -> None:
    record()
    entry = stored_entries(audit_store)[0]
    entry["action"] = "pay_now"
    persist(audit_store, entry)
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["broken_at_index"] == 0
    evidence = result["tamper_evidence"][0]
    assert evidence["detail"] == "entry_hash mismatch"
    assert evidence["decision_id"] == entry["decision_id"]
    assert evidence["expected_hash"] != evidence["actual_hash"]


def test_verify_chain_tampered_link(audit_store: GraphStore) -> None:
    record(0)
    record(1)
    entry = stored_entries(audit_store)[1]
    entry["prev_hash"] = "broken"
    # Re-seal to isolate the link check from the entry-hash check.
    model = LedgerEntry(**{field.name: entry[field.name] for field in fields(LedgerEntry)})
    entry["entry_hash"] = model.compute_hash()
    persist(audit_store, entry)
    result = audit.verify_chain()
    assert result["verified"] is False
    assert any(item["detail"] == "prev_hash linkage mismatch" for item in result["tamper_evidence"])


@pytest.mark.parametrize("index", [0, 1, 2])
def test_verify_chain_deleted_entry(audit_store: GraphStore, index: int) -> None:
    for i in range(3):
        record(i)
    entry = stored_entries(audit_store)[index]
    audit_store.delete_ledger("s2p", entry["key"])
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["tamper_evidence"]


def test_verify_chain_deleted_all_entries(audit_store: GraphStore) -> None:
    record()
    for entry in stored_entries(audit_store):
        audit_store.delete_ledger("s2p", entry["key"])
    result = audit.verify_chain()
    assert result["verified"] is False
    assert any(item["detail"] == "persisted chain head mismatch" for item in result["tamper_evidence"])


def test_verify_chain_outcome_tamper(audit_store: GraphStore) -> None:
    decision = record()
    audit.record_outcome(decision["decision_id"], "confirmed")
    entry = stored_entries(audit_store)[1]
    entry["outcome"] = "overridden"
    persist(audit_store, entry)
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["tamper_evidence"][0]["type"] == "outcome"
    assert result["tamper_evidence"][0]["detail"] == "entry_hash mismatch"


def test_verify_chain_outcome_reference_tamper(audit_store: GraphStore) -> None:
    decision = record()
    audit.record_outcome(decision["decision_id"], "confirmed")
    entry = stored_entries(audit_store)[1]
    entry["decision_entry_hash"] = "wrong-decision"
    model = OutcomeEntry(**{field.name: entry[field.name] for field in fields(OutcomeEntry)})
    entry["entry_hash"] = model.compute_hash()
    persist(audit_store, entry)
    result = audit.verify_chain()
    assert result["verified"] is False
    assert any(item["detail"] == "decision_entry_hash linkage mismatch" for item in result["tamper_evidence"])


def test_verify_chain_empty() -> None:
    result = audit.verify_chain()
    assert result["verified"] is True
    assert result["entries_checked"] == 0
    assert result["tamper_evidence"] == []


def test_verify_chain_store_unavailable(tmp_path: Path) -> None:
    # Real closed database, not a mocked reader returning an invented failure.
    unavailable = SQLiteGraphStore(tmp_path / "closed.db", domain="s2p")
    unavailable.close()
    result = audit.verify_chain(unavailable)
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"
    assert str(tmp_path) not in str(result)


def test_verify_chain_unconfigured() -> None:
    audit.configure_graph_store(None)
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"


def test_verify_chain_unsealed_legacy_decisions(audit_store: GraphStore) -> None:
    audit_store.write_decision(domain="s2p", category="price_variance", action="hold_for_review",
                               confidence=0.8, factors={"amount_variance_ratio": 0.5})
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"
    assert result["unsealed_decisions"] == 1
    assert stored_entries(audit_store) == []  # Verification must not invent history.


def test_verify_chain_missing_field_fails_closed(audit_store: GraphStore) -> None:
    record()
    entry = stored_entries(audit_store)[0]
    del entry["entry_hash"]
    persist(audit_store, entry)
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"


def test_verify_chain_archived_decision_still_verifies(audit_store: GraphStore) -> None:
    record()
    assert isinstance(audit_store, (InMemoryGraphStore, SQLiteGraphStore))
    assert audit_store.archive_decisions("s2p", before=1e12, status_filter="pending") == 1
    assert audit_store.get_all_decisions("s2p") == []
    result = audit.verify_chain()
    assert result["verified"] is True
    assert result["entries_checked"] == 1


def test_verify_chain_unsealed_archives_fail_closed(audit_store: GraphStore) -> None:
    audit_store.write_decision(domain="s2p", category="price_variance", action="hold_for_review",
                               confidence=0.8, factors={"amount_variance_ratio": 0.5})
    assert isinstance(audit_store, (InMemoryGraphStore, SQLiteGraphStore))
    assert audit_store.archive_decisions("s2p", before=1e12, status_filter="pending") == 1
    assert audit_store.get_all_decisions("s2p") == []
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"
    assert result["unsealed_decisions"] == 1


def test_verify_chain_missing_head(audit_store: GraphStore) -> None:
    record()
    audit_store.delete_governance("s2p", "s2p-audit-head")
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["tamper_evidence"]


def test_verify_chain_incomplete_write(audit_store: GraphStore) -> None:
    record()
    audit_store.save_governance("s2p", "s2p-audit-pending:interrupted", {"operation": "outcome"})
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"
    assert result["incomplete_writes"] == 1


def test_verify_chain_unhashed_decision_display_fields(audit_store: GraphStore) -> None:
    record()
    entry = stored_entries(audit_store)[0]
    entry["outcome"] = "overridden"
    persist(audit_store, entry)
    result = audit.verify_chain()
    assert result["verified"] is False
    assert result["tamper_evidence"][0]["detail"] == "decision outcome fields mutated"


def test_verify_chain_ignores_other_ledger_namespaces(audit_store: GraphStore) -> None:
    record()
    audit_store.save_ledger("s2p", "compounding-ledger", {"accuracy": 0.8})
    audit_store.save_ledger("another-domain", "s2p-audit-entry:foreign", {"type": "unknown"})
    assert audit.verify_chain()["verified"] is True


def test_verify_endpoint_returns_result(audit_store: GraphStore) -> None:
    record()
    app = FastAPI()
    app.state.graph_store = audit_store
    app.include_router(router)
    # The HTTP endpoint must use the request's store, not the module singleton.
    audit.configure_graph_store(InMemoryGraphStore(domain="s2p"))
    with TestClient(app) as client:
        result = client.post("/api/s2p/audit/verify")
        assert result.status_code == 200
        assert result.json()["verified"] is True
        assert result.json()["entries_checked"] == 1
        entry = stored_entries(audit_store)[0]
        entry["confidence"] = 0.0
        persist(audit_store, entry)
        tampered = client.post("/api/s2p/audit/verify")
        assert tampered.status_code == 200
        assert tampered.json()["verified"] is False
        assert tampered.json()["tamper_evidence"]


def test_verify_endpoint_without_store_fails_closed() -> None:
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        result = client.post("/api/s2p/audit/verify").json()
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"


def test_sqlite_restart_retains_chain(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    first = SQLiteGraphStore(path, domain="s2p")
    audit.configure_graph_store(first)
    decision = record()
    audit.record_outcome(decision["decision_id"], "confirmed")
    first.close()
    reopened = SQLiteGraphStore(path, domain="s2p")
    try:
        audit.configure_graph_store(reopened)
        result = audit.verify_chain()
        assert result["verified"] is True
        assert result["entries_checked"] == 2
    finally:
        audit.configure_graph_store(None)
        reopened.close()


def test_epoch_archive_creates_snapshot() -> None:
    decision = record()
    audit.record_outcome(decision["decision_id"], "confirmed")
    archive = audit.create_epoch_archive("operator_snapshot")
    archives = audit.get_epoch_archives()
    assert archive["epoch"] == 1
    assert archive["entry_count"] == 2
    assert archive["verified"] is True
    assert archives[0]["entry_count"] == 2
    assert [entry["type"] for entry in archives[0]["entries"] if "type" in entry] == ["outcome"]
    assert audit.verify_chain()["archived_epochs"] == 1


def test_async_concurrent_write_paths_preserve_chain() -> None:
    async def write_all() -> list[dict[str, Any]]:
        return list(await asyncio.gather(*(
            audit.async_record_decision(f"PO-ASYNC-{i}", "price_variance", "hold_for_review",
                                        ["amount_variance_ratio"], 0.84) for i in range(5)
        )))
    assert len(asyncio.run(write_all())) == 5
    assert audit.verify_chain()["verified"] is True
    assert audit.verify_chain()["chain_length"] == 5


def test_threaded_writes_preserve_chain() -> None:
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(list(pool.map(record, range(8)))) == 8
    result = audit.verify_chain()
    assert result["verified"] is True
    assert result["entries_checked"] == 8


def test_no_pytest_detection() -> None:
    source = (BACKEND_ROOT / "app/framework/audit.py").read_text(encoding="utf-8")
    assert "sys.modules" not in source
    assert "PYTEST_CURRENT_TEST" not in source
    assert "pytest" not in source
    assert "_LEDGER" not in source


def test_no_soc_vocabulary_in_backported_framework_files() -> None:
    framework_dir = BACKEND_ROOT / "app" / "framework"
    forbidden = ["credential_access", "malware_execution", "data_exfiltration",
                 "lateral_movement", "privilege_escalation", "suppress", "refer_to_analyst"]
    matches: list[str] = []
    for filename in ["audit.py", "composite_gate.py", "intervention_controls.py"]:
        source = (framework_dir / filename).read_text(encoding="utf-8")
        matches.extend(f"{filename}:{term}" for term in forbidden if term in source)
    assert matches == []
