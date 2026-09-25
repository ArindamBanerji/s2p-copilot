"""S2P audit: one persisted hash chain and one verifier for every GraphStore.

Legacy decision rows without sealed audit entries cannot be certified
retrospectively. No environment-dependent or in-memory fallback ledger exists.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, fields
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Coroutine, TypeAlias, TypeVar, Union, cast
from uuid import uuid4

from ci_platform.audit.evidence_ledger import LedgerEntry, OutcomeEntry
from copilot_sdk.graph.protocol import GraphStore

AuditEntry: TypeAlias = Union[LedgerEntry, OutcomeEntry]
T = TypeVar("T")
_DOMAIN = "s2p"
_GRAPH_STORE: GraphStore | None = None
_ENTRY_PREFIX = "s2p-audit-entry:"
_HEAD_KEY = "s2p-audit-head"
_ARCHIVE_PREFIX = "s2p-audit-archive:"
_PENDING_PREFIX = "s2p-audit-pending:"
_GENESIS = "0" * 64
# Serializes sync and async callers in this process. Cross-process conflicting
# appends fail verification (indices/head). GraphStore has no atomic append API.
_ledger_lock = RLock()

_REQUEST_DEFAULTS: dict[str, dict[str, Any]] = {
    "PO-1001": {"situation_type": "price_variance", "action_taken": "hold_for_review",
                "factors": ["amount_variance_ratio", "supplier_risk_rating"], "confidence": 0.85},
    "PO-1002": {"situation_type": "duplicate_risk", "action_taken": "flag_leakage",
                "factors": ["duplicate_score", "vendor_decisions"], "confidence": 0.90},
}
_DEFAULT_CTX: dict[str, Any] = {
    "situation_type": "format_compliance", "action_taken": "hold_for_review",
    "factors": ["manual_review_required"], "confidence": 0.60,
}


def configure_graph_store(graph_store: GraphStore | None) -> None:
    """Inject persistence explicitly. None unconfigures, never enables a fallback."""
    global _GRAPH_STORE
    with _ledger_lock:
        _GRAPH_STORE = graph_store


def _graph_store() -> GraphStore:
    if _GRAPH_STORE is None:
        raise RuntimeError("S2P audit GraphStore has not been configured")
    return _GRAPH_STORE


def _run_sync(coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("Use the async audit API from inside a running event loop")


def _read_entries(store: GraphStore) -> list[AuditEntry]:
    entries: list[AuditEntry] = []
    for row in store.list_ledgers(_DOMAIN):
        if not str(row.get("key", "")).startswith(_ENTRY_PREFIX):
            continue  # Compounding ledgers share the storage namespace.
        kind = row.get("type")
        if kind not in {"decision", "outcome"}:
            raise ValueError("Unknown audit entry type")
        model = LedgerEntry if kind == "decision" else OutcomeEntry
        # Require original hashes and indices. Never seal on read or fill missing
        # fields with defaults: damaged/incomplete records must fail closed.
        payload = {field.name: row[field.name] for field in fields(model)}
        entry = model(**payload)
        if type(entry.chain_index) is not int or entry.chain_index < 0:
            raise ValueError("Invalid audit sequence")
        entries.append(entry)
    return sorted(entries, key=lambda entry: entry.chain_index)


def compute_hash(entry: AuditEntry) -> str:
    return str(entry.compute_hash())


def _verify_entries(entries: list[AuditEntry], head: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The single verification algorithm, independent of storage adapter."""
    evidence: list[dict[str, Any]] = []
    previous = _GENESIS
    decisions: dict[str, str] = {}
    for index, entry in enumerate(entries):
        base = {"index": index, "type": "outcome" if isinstance(entry, OutcomeEntry) else "decision",
                "decision_id": entry.decision_id}
        expected_hash = compute_hash(entry)
        if entry.entry_hash != expected_hash:
            evidence.append({**base, "detail": "entry_hash mismatch",
                             "expected_hash": expected_hash, "actual_hash": entry.entry_hash})
        if entry.prev_hash != previous:
            evidence.append({**base, "detail": "prev_hash linkage mismatch",
                             "expected_prev_hash": previous, "actual_prev_hash": entry.prev_hash})
        if entry.chain_index != index:
            evidence.append({**base, "detail": "chain_index mismatch"})
        if isinstance(entry, OutcomeEntry):
            if decisions.get(entry.decision_id) != entry.decision_entry_hash:
                evidence.append({**base, "detail": "decision_entry_hash linkage mismatch"})
        else:
            if entry.decision_id in decisions:
                evidence.append({**base, "detail": "duplicate decision entry"})
            # LedgerEntry excludes these legacy display fields from its hash;
            # outcomes must instead be separate sealed events.
            if entry.outcome != "pending" or entry.analyst_override is not False:
                evidence.append({**base, "detail": "decision outcome fields mutated"})
            decisions[entry.decision_id] = entry.entry_hash
        previous = entry.entry_hash
    # Detect tail truncation, including removal of the only entry. This local
    # head is not an external trust anchor against rewriting BOTH head and chain.
    if (head is None and entries) or (head is not None and (
        head.get("count") != len(entries) or head.get("entry_hash") != previous
    )):
        evidence.append({"index": len(entries), "detail": "persisted chain head mismatch"})
    return evidence


def _append_entry(store: GraphStore, entry: AuditEntry) -> None:
    entries = _read_entries(store)
    if _verify_entries(entries, store.get_governance(_DOMAIN, _HEAD_KEY)):
        raise RuntimeError("Cannot append to an invalid audit chain")
    entry.chain_index = len(entries)
    entry.prev_hash = entries[-1].entry_hash if entries else _GENESIS
    entry.seal()
    # Unique keys preserve both records in a cross-worker race: duplicate indices
    # fail verification rather than silently overwriting another worker's entry.
    store.save_ledger(_DOMAIN, _ENTRY_PREFIX + uuid4().hex, {
        "type": "outcome" if isinstance(entry, OutcomeEntry) else "decision", **asdict(entry),
    })
    store.save_governance(_DOMAIN, _HEAD_KEY, {"count": len(entries) + 1, "entry_hash": entry.entry_hash})


def _entry_to_dict(entry: AuditEntry) -> dict[str, Any]:
    result = asdict(entry)
    result.update(domain=_DOMAIN, hash=entry.entry_hash)
    if isinstance(entry, OutcomeEntry):
        result["type"] = "outcome"
    else:
        result.update(id=entry.decision_id, action_taken=entry.action,
                      factors=list(entry.factor_breakdown), analyst_confirmed=False, outcome=None)
    return result


def _graph_decision_row(decision: dict[str, Any]) -> dict[str, Any]:
    row = dict(decision)
    decision_id = str(row.get("decision_id") or row.get("id") or "")
    metadata = row.get("metadata") or {}
    row.update(domain=_DOMAIN, id=decision_id, decision_id=decision_id,
               alert_id=metadata.get("audit_alert_id") or row.get("alert_id") or decision_id,
               situation_type=row.get("category") or "unknown",
               action_taken=row.get("action_taken") or row.get("recommended_action") or row.get("action"),
               factors=row.get("factors") or row.get("factor_names") or [])
    return row


async def async_record_decision(
    alert_id: str, situation_type: str, action_taken: str, factors: list[str], confidence: float,
    kernel_type: str | None = None, noise_zone: str | None = None,
    conservation_status: str | None = None,
) -> dict[str, Any]:
    with _ledger_lock:
        store = _graph_store()
        metadata = {key: value for key, value in {
            "audit_alert_id": alert_id, "kernel_type": kernel_type,
            "noise_zone": noise_zone, "conservation_status": conservation_status,
        }.items() if value is not None}
        factor_values = {factor: 1.0 for factor in factors}
        pending_key = _PENDING_PREFIX + uuid4().hex
        store.save_governance(_DOMAIN, pending_key, {"operation": "decision"})
        decision_id = store.write_decision(
            domain=_DOMAIN, category=situation_type, action=action_taken,
            confidence=confidence, factors=factor_values, metadata=metadata,
        )
        entry = LedgerEntry(
            decision_id=decision_id, timestamp=datetime.now(timezone.utc).isoformat(),
            alert_id=alert_id, factor_breakdown=factor_values, action=action_taken,
            confidence=confidence, outcome="pending", analyst_override=False,
            centroid_state_hash="", prev_hash=_GENESIS, kernel_type=kernel_type,
            noise_zone=noise_zone, conservation_status=conservation_status,
        )
        _append_entry(store, entry)
        store.delete_governance(_DOMAIN, pending_key)
        return {**_entry_to_dict(entry), "situation_type": situation_type}


def record_decision(
    alert_id: str, situation_type: str, action_taken: str, factors: list[str], confidence: float,
    kernel_type: str | None = None, noise_zone: str | None = None,
    conservation_status: str | None = None,
) -> dict[str, Any]:
    return _run_sync(async_record_decision(alert_id, situation_type, action_taken, factors,
                                         confidence, kernel_type, noise_zone, conservation_status))


async def async_record_outcome(
    decision_id: str, outcome: str, analyst_override: bool = False,
) -> dict[str, Any] | None:
    with _ledger_lock:
        store = _graph_store()
        decisions = [entry for entry in _read_entries(store) if isinstance(entry, LedgerEntry)]
        decision = next((entry for entry in decisions if entry.decision_id == decision_id), None)
        if decision is None:
            decision = next((entry for entry in reversed(decisions) if entry.alert_id == decision_id), None)
        if decision is None:
            return None  # Never fabricate a sealed entry for a legacy decision.
        # GraphStore has no multi-record transaction. Leave this marker in place
        # after any partial write so an unsealed outcome cannot verify cleanly.
        pending_key = _PENDING_PREFIX + uuid4().hex
        store.save_governance(_DOMAIN, pending_key, {"operation": "outcome", "decision_id": decision.decision_id})
        store.write_outcome(
            decision_id=decision.decision_id, actual_action=decision.action,
            is_correct=outcome == "confirmed", metadata={"analyst_override": analyst_override},
            domain=_DOMAIN, outcome=outcome, analyst_action=decision.action,
            final_action=decision.action, recommended_action=decision.action,
            was_override=analyst_override,
        )
        entry = OutcomeEntry(
            chain_index=0, decision_id=decision.decision_id, decision_entry_hash=decision.entry_hash,
            outcome=outcome, analyst_override=analyst_override,
            timestamp=datetime.now(timezone.utc).isoformat(), prev_hash=_GENESIS,
        )
        _append_entry(store, entry)
        store.delete_governance(_DOMAIN, pending_key)
        return {**_entry_to_dict(entry), "alert_id": decision.alert_id}


def record_outcome(decision_id: str, outcome: str, analyst_notes: str | None = None) -> dict[str, Any] | None:
    return _run_sync(async_record_outcome(decision_id, outcome, True))


def get_decision_rows() -> list[dict[str, Any]]:
    return [_graph_decision_row(row) for row in reversed(_graph_store().get_decisions(domain=_DOMAIN, limit=400))]


def get_decisions() -> list[dict[str, Any]]:
    return get_decision_rows()


def get_audit_entries(store: GraphStore | None = None) -> list[dict[str, Any]]:
    with _ledger_lock:
        return [_entry_to_dict(entry) for entry in _read_entries(store if store is not None else _graph_store())]


def verify_chain(store: GraphStore | None = None) -> dict[str, Any]:
    """Read and verify, failing closed on missing history or storage/read errors."""
    result: dict[str, Any] = {
        "chain_length": 0, "entries_checked": 0, "verified": False, "tamper_evidence": [],
        "first_record": None, "last_record": None, "epoch": 1, "archived_epochs": 0,
    }
    try:
        with _ledger_lock:
            configured = store if store is not None else _graph_store()
            governance = configured.list_governance(_DOMAIN)
            pending = [row for row in governance if str(row.get("key", "")).startswith(_PENDING_PREFIX)]
            archives = [row for row in governance if str(row.get("key", "")).startswith(_ARCHIVE_PREFIX)]
            result.update(archived_epochs=len(archives), epoch=len(archives) + 1)
            entries = _read_entries(configured)
            evidence = _verify_entries(entries, configured.get_governance(_DOMAIN, _HEAD_KEY))
            result.update(chain_length=len(entries), entries_checked=len(entries), tamper_evidence=evidence,
                          first_record=entries[0].timestamp if entries else None,
                          last_record=entries[-1].timestamp if entries else None)
            # Empty sealed history is valid only when the domain really is empty.
            sealed_ids = {entry.decision_id for entry in entries if isinstance(entry, LedgerEntry)}
            # Archival removes decisions from the active view, not from audit
            # history. Include both views to avoid false failures and to avoid
            # certifying an empty active store whose archives remain unsealed.
            decision_rows = configured.get_all_decisions(_DOMAIN) + configured.get_archived_decisions(_DOMAIN)
            stored_ids = {str(row.get("decision_id") or row.get("id") or "")
                          for row in decision_rows}
            missing = stored_ids - sealed_ids
            if missing or pending:
                result.update(reason="verification_unavailable", unsealed_decisions=len(missing),
                              incomplete_writes=len(pending))
            else:
                result["verified"] = not evidence
            if sealed_ids - stored_ids:
                evidence.append({"index": 0, "detail": "sealed decision missing from store"})
                result["verified"] = False
            if evidence:
                result["broken_at_index"] = evidence[0]["index"]
            return result
    except Exception as exc:
        # Do not expose backend paths, credentials, queries, or exception messages.
        result.update(verified=False, reason="verification_unavailable", error=type(exc).__name__)
        return result


async def async_reconstruct_from_memory() -> int:
    from app.framework.feedback_store import FEEDBACK_GIVEN

    store = _graph_store()
    added = 0
    for alert_id, feedback in FEEDBACK_GIVEN.items():
        existing = [row for row in store.get_decisions(domain=_DOMAIN, limit=400)
                    if row.get("alert_id") == alert_id
                    or row.get("metadata", {}).get("audit_alert_id") == alert_id]
        if existing:
            continue
        ctx = _REQUEST_DEFAULTS.get(alert_id, _DEFAULT_CTX)
        decision = await async_record_decision(alert_id=alert_id, **ctx)
        outcome = feedback.get("outcome")
        if outcome:
            await async_record_outcome(decision["decision_id"], str(outcome), True)
            added += 1
    return added


def reconstruct_from_memory() -> int:
    return _run_sync(async_reconstruct_from_memory())


async def async_create_epoch_archive(reason: str = "manual_snapshot") -> dict[str, Any]:
    with _ledger_lock:
        store = _graph_store()
        entries = get_audit_entries(store)
        archive = {"epoch": len(get_epoch_archives()) + 1, "reason": reason,
                   "entry_count": len(entries), "created_at": datetime.now(timezone.utc).isoformat(),
                   "verified": verify_chain(store)["verified"], "entries": entries}
        store.save_governance(_DOMAIN, _ARCHIVE_PREFIX + uuid4().hex, archive)
        return {key: value for key, value in archive.items() if key != "entries"}


def create_epoch_archive(reason: str = "manual_snapshot") -> dict[str, Any]:
    return _run_sync(async_create_epoch_archive(reason))


def get_epoch_archives() -> list[dict[str, Any]]:
    rows = [row for row in _graph_store().list_governance(_DOMAIN)
            if str(row.get("key", "")).startswith(_ARCHIVE_PREFIX)]
    return sorted(rows, key=lambda row: row["epoch"])


async def async_reset_audit_state() -> None:
    """Retain the existing domain-scoped reset compatibility operation."""
    with _ledger_lock:
        cast(Any, _graph_store()).domain_scoped_reset(_DOMAIN)


def reset_audit_state() -> None:
    _run_sync(async_reset_audit_state())


async def async_record_reset_marker(mode: str) -> None:
    await async_record_decision("__RESET__", "audit_reset", f"reset_{mode}", [f"mode={mode}"], 1.0)


def record_reset_marker(mode: str) -> None:
    _run_sync(async_record_reset_marker(mode))
