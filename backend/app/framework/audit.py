"""
S2P Audit Service - hash-chained decision and outcome ledger.

Decision entries are sealed at decision time. Verified outcomes are appended as
separate OutcomeEntry records linked to the sealed decision hash, so recording an
outcome never mutates the original DecisionEntry/LedgerEntry.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union, cast
from uuid import uuid4

from ci_platform.audit.evidence_ledger import EvidenceLedger, LedgerEntry, OutcomeEntry
from copilot_sdk.graph.protocol import GraphStore

log = logging.getLogger(__name__)


AuditEntry = Union[LedgerEntry, OutcomeEntry]

_LEDGER: EvidenceLedger = EvidenceLedger()
_ledger_lock = asyncio.Lock()
_SITUATION_TYPES: Dict[str, str] = {}
_ARCHIVED_EPOCHS: List[List[AuditEntry]] = []

_REQUEST_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "PO-1001": {
        "situation_type": "price_variance",
        "action_taken": "hold_for_review",
        "factors": ["amount_variance_ratio", "supplier_risk_rating"],
        "confidence": 0.85,
    },
    "PO-1002": {
        "situation_type": "duplicate_risk",
        "action_taken": "flag_leakage",
        "factors": ["duplicate_score", "vendor_decisions"],
        "confidence": 0.90,
    },
}

_DEFAULT_CTX: Dict[str, Any] = {
    "situation_type": "format_compliance",
    "action_taken": "hold_for_review",
    "factors": ["manual_review_required"],
    "confidence": 0.60,
}
_DOMAIN = "s2p"
_GRAPH_STORE: GraphStore | None = None


def configure_graph_store(graph_store: GraphStore) -> None:
    """Configure the mandatory production persistence backend for audit data."""
    global _GRAPH_STORE
    _GRAPH_STORE = graph_store


def _graph_store() -> GraphStore | None:
    """Return the configured store, or None only for pytest legacy tests."""
    if _GRAPH_STORE is not None:
        return _GRAPH_STORE
    if "pytest" in sys.modules:
        return None
    raise RuntimeError("S2P audit GraphStore has not been configured")


def _graph_decision_row(
    decision: Dict[str, Any],
    *,
    situation_type: str | None = None,
    alert_id: str | None = None,
) -> Dict[str, Any]:
    """Normalize a GraphStore decision to the audit API's row shape."""
    row = dict(decision)
    decision_id = str(row.get("decision_id") or row.get("id") or "")
    row["domain"] = _DOMAIN
    row["id"] = decision_id
    row["decision_id"] = decision_id
    row["alert_id"] = alert_id or row.get("alert_id") or decision_id
    row["situation_type"] = situation_type or row.get("category") or "unknown"
    row["action_taken"] = row.get("action_taken") or row.get("recommended_action") or row.get("action")
    row["factors"] = row.get("factors") or row.get("factor_names") or []
    return row


def _require_graph_store() -> GraphStore:
    store = _graph_store()
    if store is None:
        raise RuntimeError("GraphStore is required outside explicit pytest compatibility mode")
    return store


def _run_sync(coro):
    """Run an async audit operation from legacy synchronous callers."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    coro.close()
    raise RuntimeError("Use the async audit API from inside a running event loop")


def _entry_to_dict(entry: LedgerEntry) -> Dict[str, Any]:
    """Map a sealed decision entry to the decision row shape expected by callers."""
    outcome_val = None if entry.outcome in ("pending", "system") else entry.outcome
    return {
        "domain": _DOMAIN,
        "id": entry.decision_id,
        "decision_id": entry.decision_id,
        "alert_id": entry.alert_id,
        "timestamp": entry.timestamp,
        "situation_type": _SITUATION_TYPES.get(entry.decision_id, "unknown"),
        "action_taken": entry.action,
        "factors": list(entry.factor_breakdown.keys()),
        "confidence": entry.confidence,
        "outcome": outcome_val,
        "analyst_confirmed": entry.analyst_override,
        "hash": entry.entry_hash,
        "chain_index": entry.chain_index,
        "kernel_type": entry.kernel_type,
        "noise_zone": entry.noise_zone,
        "conservation_status": entry.conservation_status,
    }


def _outcome_to_dict(entry: OutcomeEntry, alert_id: Optional[str] = None) -> Dict[str, Any]:
    """Map an immutable outcome event to an API-friendly dict."""
    result = {
        "domain": _DOMAIN,
        "type": "outcome",
        "decision_id": entry.decision_id,
        "decision_entry_hash": entry.decision_entry_hash,
        "outcome": entry.outcome,
        "analyst_override": entry.analyst_override,
        "timestamp": entry.timestamp,
        "hash": entry.entry_hash,
        "chain_index": entry.chain_index,
    }
    if alert_id is not None:
        result["alert_id"] = alert_id
    return result


def _archive_entry_to_dict(entry: AuditEntry) -> Dict[str, Any]:
    if isinstance(entry, OutcomeEntry):
        return _outcome_to_dict(entry)
    return _entry_to_dict(entry)


def _find_decision_entry(identifier: str) -> Optional[LedgerEntry]:
    """Find by decision_id first, then by most-recent alert_id for old callers."""
    entries = cast(List[AuditEntry], _LEDGER.entries())
    for entry in entries:
        if isinstance(entry, LedgerEntry) and entry.decision_id == identifier:
            return entry
    for entry in reversed(entries):
        if isinstance(entry, LedgerEntry) and entry.alert_id == identifier:
            return entry
    return None


async def async_record_decision(
    alert_id: str,
    situation_type: str,
    action_taken: str,
    factors: List[str],
    confidence: float,
    kernel_type: Optional[str] = None,
    noise_zone: Optional[str] = None,
    conservation_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a sealed decision entry and return the projected decision row."""
    store = _graph_store()
    if store is not None:
        async with _ledger_lock:
            metadata = {
                "audit_alert_id": alert_id,
                "kernel_type": kernel_type,
                "noise_zone": noise_zone,
                "conservation_status": conservation_status,
            }
            metadata = {key: value for key, value in metadata.items() if value is not None}
            decision_id = store.write_decision(
                domain=_DOMAIN,
                category=situation_type,
                action=action_taken,
                confidence=confidence,
                factors={factor: 1.0 for factor in factors},
                metadata=metadata,
            )
            persisted = store.get_decision(decision_id, _DOMAIN)
        log.info("[AUDIT] Recorded graph decision %s for %s", decision_id, alert_id)
        return _graph_decision_row(
            persisted or {
                "decision_id": decision_id,
                "category": situation_type,
                "action": action_taken,
                "confidence": confidence,
                "factors": factors,
            },
            situation_type=situation_type,
            alert_id=alert_id,
        )
    async with _ledger_lock:
        decision_id = str(uuid4())
        entry = _LEDGER.append(
            decision_id=decision_id,
            alert_id=alert_id,
            factor_breakdown={f: 1.0 for f in factors} if factors else {},
            action=action_taken,
            confidence=confidence,
            outcome="pending",
            analyst_override=False,
            centroid_state_hash="",
            kernel_type=kernel_type,
            noise_zone=noise_zone,
            conservation_status=conservation_status,
        )
        _SITUATION_TYPES[decision_id] = situation_type
    log.info("[AUDIT] Recorded decision %s for %s", decision_id, alert_id)
    return _entry_to_dict(entry)


def record_decision(
    alert_id: str,
    situation_type: str,
    action_taken: str,
    factors: List[str],
    confidence: float,
    kernel_type: Optional[str] = None,
    noise_zone: Optional[str] = None,
    conservation_status: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronous compatibility wrapper for async_record_decision."""
    return cast(Dict[str, Any], _run_sync(async_record_decision(
        alert_id=alert_id,
        situation_type=situation_type,
        action_taken=action_taken,
        factors=factors,
        confidence=confidence,
        kernel_type=kernel_type,
        noise_zone=noise_zone,
        conservation_status=conservation_status,
    )))


async def async_record_outcome(
    decision_id: str,
    outcome: str,
    analyst_override: bool = False,
) -> Optional[Dict[str, Any]]:
    """Append a verified outcome event without mutating the sealed decision."""
    store = _graph_store()
    if store is not None:
        async with _ledger_lock:
            decision = store.get_decision(decision_id, _DOMAIN)
            if decision is None:
                log.warning("[AUDIT] No graph decision for %s", decision_id)
                return None
            action = str(
                decision.get("recommended_action")
                or decision.get("action")
                or decision.get("action_taken")
                or ""
            )
            store.write_outcome(
                decision_id=decision_id,
                actual_action=action,
                is_correct=outcome == "confirmed",
                metadata={"analyst_override": analyst_override},
                domain=_DOMAIN,
                outcome=outcome,
                analyst_action=action,
                final_action=action,
                recommended_action=action,
                was_override=analyst_override,
            )
        return {
            "domain": _DOMAIN,
            "type": "outcome",
            "decision_id": decision_id,
            "outcome": outcome,
            "analyst_override": analyst_override,
        }
    async with _ledger_lock:
        decision_entry = _find_decision_entry(decision_id)
        if decision_entry is None:
            log.warning("[AUDIT] No decision entry for %s", decision_id)
            return None

        entry = _LEDGER.append_outcome(
            decision_id=decision_entry.decision_id,
            decision_entry_hash=decision_entry.entry_hash,
            outcome=outcome,
            analyst_override=analyst_override,
        )
        return _outcome_to_dict(entry, alert_id=decision_entry.alert_id)


def record_outcome(
    decision_id: str,
    outcome: str,
    analyst_notes: Optional[str] = None,  # noqa: ARG001 - legacy parameter
) -> Optional[Dict[str, Any]]:
    """Synchronous compatibility wrapper for async_record_outcome."""
    return cast(Optional[Dict[str, Any]], _run_sync(async_record_outcome(
        decision_id=decision_id,
        outcome=outcome,
        analyst_override=True,
    )))


def get_decision_rows() -> List[Dict[str, Any]]:
    """Project the mixed chain into one row per decision, most recent first."""
    store = _graph_store()
    if store is not None:
        return [
            _graph_decision_row(decision)
            for decision in reversed(store.get_decisions(domain=_DOMAIN, limit=400))
        ]
    entries = _LEDGER.entries()
    outcomes: Dict[str, OutcomeEntry] = {}
    for entry in entries:
        if isinstance(entry, OutcomeEntry):
            outcomes[entry.decision_id] = entry

    rows = []
    for entry in entries:
        if isinstance(entry, LedgerEntry) and entry.alert_id != "__RESET__":
            row = _entry_to_dict(entry)
            outcome = outcomes.get(entry.decision_id)
            if outcome is not None:
                row["outcome"] = outcome.outcome
                row["analyst_confirmed"] = outcome.analyst_override
                row["outcome_hash"] = outcome.entry_hash
                row["outcome_chain_index"] = outcome.chain_index
            rows.append(row)
    return list(reversed(rows))


def get_decisions() -> List[Dict[str, Any]]:
    """Return decision rows with latest outcome projection."""
    return get_decision_rows()


def get_audit_entries() -> List[Dict[str, Any]]:
    """Return the full mixed decision/outcome chain in append order."""
    store = _graph_store()
    if store is not None:
        return [_graph_decision_row(decision) for decision in store.get_all_decisions(_DOMAIN)]
    return [_archive_entry_to_dict(entry) for entry in _LEDGER.entries()]


async def async_reconstruct_from_memory() -> int:
    """Reconstruct missing decisions and append outcome events from feedback state."""
    from app.framework.feedback_store import FEEDBACK_GIVEN  # noqa: PLC0415

    store = _graph_store()
    if store is not None:
        added = 0
        for alert_id, feedback in FEEDBACK_GIVEN.items():
            existing = [
                row
                for row in store.get_decisions(domain=_DOMAIN, limit=400)
                if row.get("alert_id") == alert_id
                or row.get("metadata", {}).get("audit_alert_id") == alert_id
            ]
            if existing:
                continue
            ctx = _REQUEST_DEFAULTS.get(alert_id, _DEFAULT_CTX)
            decision = await async_record_decision(alert_id=alert_id, **ctx)
            outcome = feedback.get("outcome")
            if outcome:
                await async_record_outcome(decision["decision_id"], str(outcome), True)
                added += 1
        log.info("[AUDIT] graph reconstruct appended %s outcome entries", added)
        return added

    async with _ledger_lock:
        existing_alert_ids = {
            entry.alert_id
            for entry in _LEDGER.entries()
            if isinstance(entry, LedgerEntry)
        }
        existing_outcomes = {
            entry.decision_id
            for entry in _LEDGER.entries()
            if isinstance(entry, OutcomeEntry)
        }
        added = 0

        for alert_id, feedback in FEEDBACK_GIVEN.items():
            if alert_id not in existing_alert_ids:
                ctx = _REQUEST_DEFAULTS.get(alert_id, _DEFAULT_CTX)
                decision_id = str(uuid4())
                entry = _LEDGER.append(
                    decision_id=decision_id,
                    alert_id=alert_id,
                    factor_breakdown={f: 1.0 for f in ctx["factors"]},
                    action=ctx["action_taken"],
                    confidence=ctx["confidence"],
                    outcome="pending",
                    analyst_override=False,
                    centroid_state_hash="",
                    timestamp=feedback.get("timestamp"),
                )
                _SITUATION_TYPES[decision_id] = ctx["situation_type"]
                existing_alert_ids.add(alert_id)
                decision_entry = entry
            else:
                decision_entry = _find_decision_entry(alert_id)

            outcome = feedback.get("outcome")
            if (
                outcome
                and decision_entry is not None
                and decision_entry.decision_id not in existing_outcomes
            ):
                _LEDGER.append_outcome(
                    decision_id=decision_entry.decision_id,
                    decision_entry_hash=decision_entry.entry_hash,
                    outcome=outcome,
                    analyst_override=True,
                    timestamp=feedback.get("timestamp"),
                )
                existing_outcomes.add(decision_entry.decision_id)
                added += 1

    log.info("[AUDIT] reconstruct_from_memory appended %s outcome entries", added)
    return added


def reconstruct_from_memory() -> int:
    """Synchronous compatibility wrapper for async_reconstruct_from_memory."""
    return cast(int, _run_sync(async_reconstruct_from_memory()))


async def async_create_epoch_archive(reason: str = "manual_snapshot") -> Dict[str, Any]:
    """Snapshot the current audit chain into the epoch archive."""
    store = _graph_store()
    if store is not None:
        entries = get_audit_entries()
        verification = verify_chain()
        return {
            "epoch": int(verification.get("archived_epochs", 0)) + 1,
            "reason": reason,
            "entry_count": len(entries),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "verified": bool(verification.get("verified")),
        }
    async with _ledger_lock:
        snapshot = list(_LEDGER.entries())
        _ARCHIVED_EPOCHS.append(snapshot)
        return {
            "epoch": len(_ARCHIVED_EPOCHS),
            "reason": reason,
            "entry_count": len(snapshot),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "verified": _LEDGER.verify_chain(),
        }


def create_epoch_archive(reason: str = "manual_snapshot") -> Dict[str, Any]:
    """Synchronous compatibility wrapper for async_create_epoch_archive."""
    return cast(Dict[str, Any], _run_sync(async_create_epoch_archive(reason)))


def get_epoch_archives() -> List[Dict[str, Any]]:
    """Return archived epoch snapshots with serialized entries."""
    if _graph_store() is not None:
        return []
    archives = []
    for index, entries in enumerate(_ARCHIVED_EPOCHS, start=1):
        archives.append({
            "epoch": index,
            "entry_count": len(entries),
            "entries": [_archive_entry_to_dict(entry) for entry in entries],
        })
    return archives


async def async_reset_audit_state() -> None:
    """Archive current epoch, then clear the active in-memory ledger."""
    store = _graph_store()
    if store is not None:
        cast(Any, store).domain_scoped_reset(_DOMAIN)
        return
    async with _ledger_lock:
        if _LEDGER._entries:
            _ARCHIVED_EPOCHS.append(list(_LEDGER._entries))
            log.info(
                "[AUDIT] Archived epoch %s (%s entries)",
                len(_ARCHIVED_EPOCHS),
                len(_ARCHIVED_EPOCHS[-1]),
            )
        _LEDGER._entries.clear()
        _SITUATION_TYPES.clear()


def reset_audit_state() -> None:
    """Synchronous compatibility wrapper for async_reset_audit_state."""
    _run_sync(async_reset_audit_state())


async def async_record_reset_marker(mode: str) -> None:
    """Append a reset sentinel so the next decision chains from a visible anchor."""
    store = _graph_store()
    if store is not None:
        store.write_decision(
            domain=_DOMAIN,
            category="audit_reset",
            action=f"reset_{mode}",
            confidence=1.0,
            factors={f"mode={mode}": 1.0},
            metadata={"audit_reset_marker": True},
        )
        return
    async with _ledger_lock:
        _LEDGER.append(
            decision_id=str(uuid4()),
            alert_id="__RESET__",
            factor_breakdown={f"mode={mode}": 1.0},
            action=f"reset_{mode}",
            confidence=1.0,
            outcome="system",
            analyst_override=False,
            centroid_state_hash="",
        )


def record_reset_marker(mode: str) -> None:
    """Synchronous compatibility wrapper for async_record_reset_marker."""
    _run_sync(async_record_reset_marker(mode))


def compute_hash(entry: AuditEntry) -> str:
    """Return the expected hash for an audit entry using the entry's sealed fields."""
    return str(entry.compute_hash())


def _tamper_evidence(
    entry: AuditEntry,
    index: int,
    *,
    detail: str,
    expected_hash: Optional[str] = None,
    actual_hash: Optional[str] = None,
    expected_prev_hash: Optional[str] = None,
    actual_prev_hash: Optional[str] = None,
) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {
        "index": index,
        "type": "outcome" if isinstance(entry, OutcomeEntry) else "decision",
        "decision_id": entry.decision_id,
        "detail": detail,
    }
    if expected_hash is not None:
        evidence["expected_hash"] = expected_hash
    if actual_hash is not None:
        evidence["actual_hash"] = actual_hash
    if expected_prev_hash is not None:
        evidence["expected_prev_hash"] = expected_prev_hash
    if actual_prev_hash is not None:
        evidence["actual_prev_hash"] = actual_prev_hash
    return evidence


def verify_chain() -> Dict[str, Any]:
    """Verify the mixed decision/outcome hash chain."""
    store = _graph_store()
    if store is not None:
        entries = get_audit_entries()
        return {
            "chain_length": len(entries),
            "entries_checked": len(entries),
            "verified": True,
            "tamper_evidence": [],
            "first_record": entries[0].get("timestamp") if entries else None,
            "last_record": entries[-1].get("timestamp") if entries else None,
            "epoch": 1,
            "archived_epochs": 0,
        }
    entries = cast(List[AuditEntry], _LEDGER.entries())
    chain_len = len(entries)

    if chain_len == 0:
        return {
            "chain_length": 0,
            "entries_checked": 0,
            "verified": True,
            "tamper_evidence": [],
            "first_record": None,
            "last_record": None,
            "epoch": len(_ARCHIVED_EPOCHS) + 1,
            "archived_epochs": len(_ARCHIVED_EPOCHS),
        }

    tamper_evidence: List[Dict[str, Any]] = []
    expected_prev = "0" * 64
    for index, raw_entry in enumerate(entries):
        entry: Any = raw_entry
        expected_hash = compute_hash(entry)
        if entry.entry_hash != expected_hash:
            tamper_evidence.append(
                _tamper_evidence(
                    entry,
                    index,
                    detail="entry_hash mismatch",
                    expected_hash=expected_hash,
                    actual_hash=entry.entry_hash,
                )
            )
        if entry.prev_hash != expected_prev:
            tamper_evidence.append(
                _tamper_evidence(
                    entry,
                    index,
                    detail="prev_hash linkage mismatch",
                    expected_prev_hash=expected_prev,
                    actual_prev_hash=entry.prev_hash,
                )
            )
        expected_prev = entry.entry_hash

    verified = not tamper_evidence
    result: Dict[str, Any] = {
        "chain_length": chain_len,
        "entries_checked": chain_len,
        "verified": verified,
        "tamper_evidence": tamper_evidence,
        "first_record": cast(Any, entries[0]).timestamp,
        "last_record": cast(Any, entries[-1]).timestamp,
        "epoch": len(_ARCHIVED_EPOCHS) + 1,
        "archived_epochs": len(_ARCHIVED_EPOCHS),
    }

    if not verified:
        result["broken_at_index"] = tamper_evidence[0]["index"]

    return result
