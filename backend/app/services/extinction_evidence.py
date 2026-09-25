"""Causal evidence for a decision class's active queue becoming empty."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


EVENT_PREFIX = "s2p:class-extinction:"


def active_decision_ids(store: Any, category: str) -> set[str]:
    rows = store.get_decisions("s2p", category=category, limit=2**31 - 1)
    return {
        str(row["decision_id"]) for row in rows
        if row.get("status") == "pending" and row.get("decision_id")
    }


def record_extinction(
    store: Any, category: str, decision_id: str, active_before: set[str],
) -> dict[str, Any] | None:
    """Called inside the outcome mutation lock, after a verified commit."""
    if active_before != {decision_id}:
        return None
    decision = store.get_decision(decision_id, domain="s2p")
    if not decision or decision.get("status") not in {"confirmed", "overridden"}:
        return None
    if decision.get("category") != category:
        return None
    if active_decision_ids(store, category):
        return None
    key = EVENT_PREFIX + decision_id
    existing = store.get_governance("s2p", key)
    if existing is not None:
        return dict(existing)
    metadata = decision.get("metadata") or {}
    context = decision.get("context") or {}
    planted = any(
        source.get("planted") is True or source.get("provenance") == "sample"
        for source in (decision, metadata, context)
        if isinstance(source, dict)
    )
    event = {
        "event_type": "class_extinction",
        "category": category,
        "start_count": len(active_before),
        "end_count": 0,
        "earning_decision_id": decision_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "source": "verified_outcome",
        "planted": planted,
        "evidence_tier": "T_S" if planted else "T_O",
    }
    store.save_governance("s2p", key, event)
    return event


def extinction_history(store: Any) -> list[dict[str, Any]]:
    """Persisted transitions, not inference from invoice-file ordering."""
    return [
        dict(row) for row in store.list_governance("s2p")
        if str(row.get("key", "")).startswith(EVENT_PREFIX)
        and row.get("event_type") == "class_extinction"
    ]
