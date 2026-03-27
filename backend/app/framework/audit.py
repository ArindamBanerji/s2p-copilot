"""
SOC Audit Service — thin adapter over ci_platform Evidence Ledger.

Hash-chain implementation lives in ci_platform.audit.evidence_ledger (EvidenceLedger /
LedgerEntry).  SOC-specific wrappers handle session state and demo defaults.

Architecture: SOC is a copilot endpoint; shared audit infrastructure lives in ci-platform.
EU AI Act Art. 15 epistemic fields (kernel_type, noise_zone, conservation_status) are
carried by LedgerEntry and surfaced in the SOC API response.

Two population paths (unchanged from before):
  1. record_decision() — called proactively when the agent decides
  2. reconstruct_from_memory() — reads FEEDBACK_GIVEN from feedback_store to
     back-fill records for decisions already made in the session
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ci_platform.audit.evidence_ledger import EvidenceLedger, LedgerEntry


# ── Module-level ledger (in-memory, demo-session scoped) ─────────────────────

_LEDGER: EvidenceLedger = EvidenceLedger()

# situation_type is SOC-specific (not in LedgerEntry); stored in parallel
_SITUATION_TYPES: Dict[str, str] = {}   # decision_id → situation_type


# ── SOC demo defaults (used by reconstruct_from_memory) ──────────────────────

_ALERT_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "ALERT-7823": {
        "situation_type": "travel_login_anomaly",
        "action_taken":   "false_positive_close",
        "factors":        [
            "user_traveling",
            "vpn_matches_location",
            "mfa_completed",
            "device_fingerprint_match",
        ],
        "confidence": 0.92,
    },
    "ALERT-7824": {
        "situation_type": "known_phishing_campaign",
        "action_taken":   "auto_remediate",
        "factors":        [
            "known_campaign_signature",
            "pattern_matched",
            "sender_domain_blocked",
        ],
        "confidence": 0.94,
    },
}

_DEFAULT_CTX: Dict[str, Any] = {
    "situation_type": "unknown",
    "action_taken":   "escalate_tier2",
    "factors":        ["manual_review_required"],
    "confidence":     0.60,
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _entry_to_dict(entry: LedgerEntry) -> Dict[str, Any]:
    """Map a LedgerEntry to the SOC DecisionRecord dict expected by callers."""
    outcome_val = None if entry.outcome in ("pending", "system") else entry.outcome
    return {
        "id":                  entry.decision_id,
        "alert_id":            entry.alert_id,
        "timestamp":           entry.timestamp,
        "situation_type":      _SITUATION_TYPES.get(entry.decision_id, "unknown"),
        "action_taken":        entry.action,
        "factors":             list(entry.factor_breakdown.keys()),
        "confidence":          entry.confidence,
        "outcome":             outcome_val,
        "analyst_confirmed":   entry.analyst_override,
        "hash":                entry.entry_hash,
        # EU AI Act Art. 15 epistemic fields from ci_platform LedgerEntry
        "kernel_type":         entry.kernel_type,
        "noise_zone":          entry.noise_zone,
        "conservation_status": entry.conservation_status,
    }


# ── Core functions ────────────────────────────────────────────────────────────

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
    """
    Append a sealed LedgerEntry to the ci_platform ledger and return it as a SOC dict.

    Intended to be called when the agent makes a decision (Tab 3 analysis).
    """
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
    print(f"[AUDIT] Recorded decision {decision_id} for {alert_id} -> {action_taken}")
    return _entry_to_dict(entry)


def record_outcome(
    alert_id: str,
    outcome: str,
    analyst_notes: Optional[str] = None,  # noqa: ARG001 — reserved for future use
) -> Optional[Dict[str, Any]]:
    """
    Find the most-recent LedgerEntry for alert_id and update its outcome.

    Mutates outcome and analyst_override; the entry_hash is NOT recomputed
    (outcome is mutable by design — only the immutable decision-time fields
    are included in the hash payload).

    Returns the updated record as a dict, or None if no record exists.
    """
    for entry in reversed(_LEDGER.entries()):
        if entry.alert_id == alert_id:
            entry.outcome = outcome
            entry.analyst_override = True
            print(f"[AUDIT] Updated outcome for {alert_id}: {outcome}")
            return _entry_to_dict(entry)
    print(f"[AUDIT] record_outcome: no record found for {alert_id}")
    return None


def get_decisions() -> List[Dict[str, Any]]:
    """Return all decision records, most recent first, excluding RESET sentinels."""
    return [
        _entry_to_dict(e)
        for e in reversed(_LEDGER.entries())
        if e.alert_id != "__RESET__"
    ]


def reconstruct_from_memory() -> int:
    """
    Back-fill the ledger from existing session state — specifically
    FEEDBACK_GIVEN in feedback_store — without modifying those modules.

    For each alert in FEEDBACK_GIVEN that is not yet in the ledger:
      • Uses demo defaults (or generic defaults) for situation_type,
        action_taken, factors, confidence.
      • Sets outcome and analyst_override from the feedback entry.

    For alerts already in the ledger but without an outcome, fills the
    outcome from FEEDBACK_GIVEN if available (entry_hash is unchanged).

    Returns the number of new records added.
    """
    from app.framework.feedback_store import FEEDBACK_GIVEN  # noqa: PLC0415

    existing_alert_ids = {e.alert_id for e in _LEDGER.entries()}
    added = 0

    for alert_id, fb in FEEDBACK_GIVEN.items():
        if alert_id not in existing_alert_ids:
            ctx = _ALERT_DEFAULTS.get(alert_id, _DEFAULT_CTX)
            decision_id = str(uuid4())
            ts = fb.get("timestamp", datetime.now(timezone.utc).isoformat())
            _LEDGER.append(
                decision_id=decision_id,
                alert_id=alert_id,
                factor_breakdown={f: 1.0 for f in ctx["factors"]},
                action=ctx["action_taken"],
                confidence=ctx["confidence"],
                outcome=fb.get("outcome") or "pending",
                analyst_override=True,
                centroid_state_hash="",
                timestamp=ts,
            )
            _SITUATION_TYPES[decision_id] = ctx["situation_type"]
            existing_alert_ids.add(alert_id)
            added += 1
        else:
            # Already have a record — back-fill outcome if missing
            for entry in reversed(_LEDGER.entries()):
                if entry.alert_id == alert_id and entry.outcome in ("pending", None):
                    if fb.get("outcome"):
                        entry.outcome = fb["outcome"]
                        entry.analyst_override = True
                    break

    print(f"[AUDIT] reconstruct_from_memory: +{added} new records ({len(_LEDGER)} total)")
    return added


def reset_audit_state() -> None:
    """Clear all decision records (demo reset)."""
    _LEDGER._entries.clear()
    _SITUATION_TYPES.clear()
    print("[AUDIT] Decision ledger cleared")


def record_reset_marker(mode: str) -> None:
    """
    Write a RESET sentinel after reset_audit_state() so the next real
    decision chains off a known anchor, not a silent genesis.

    The marker uses alert_id='__RESET__' so callers can filter it out.
    Called by StateManager after clearing the ledger.
    """
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
    print(f"[AUDIT] RESET marker written (mode={mode})")


def verify_chain() -> Dict[str, Any]:
    """
    Verify the SHA-256 hash chain via ci_platform EvidenceLedger.

    Wraps the ci_platform bool result in the SOC response dict shape
    that audit.py router consumers expect:
        {
          "chain_length":    int,
          "verified":        bool,
          "first_record":    ISO timestamp | None,
          "last_record":     ISO timestamp | None,
          "broken_at_index": int   (only present when verified=False)
        }
    """
    entries = _LEDGER.entries()
    chain_len = len(entries)

    if chain_len == 0:
        return {"chain_length": 0, "verified": True, "first_record": None, "last_record": None}

    verified = _LEDGER.verify_chain()
    result: Dict[str, Any] = {
        "chain_length": chain_len,
        "verified":     verified,
        "first_record": entries[0].timestamp,
        "last_record":  entries[-1].timestamp,
    }

    if not verified:
        # Locate the broken link using LedgerEntry.is_valid() from ci_platform
        expected_prev = "0" * 64
        for i, entry in enumerate(entries):
            if not entry.is_valid() or entry.prev_hash != expected_prev:
                result["broken_at_index"] = i
                break
            expected_prev = entry.entry_hash
        print(f"[AUDIT] Chain broken at index {result.get('broken_at_index', '?')}")
    else:
        print(f"[AUDIT] Chain verified - {chain_len} records intact")

    return result
