"""Restart-safe, source-reconciled S2P compounding ledger."""

from __future__ import annotations

import json
import math
import logging
import sqlite3
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Mapping

from app.domains.s2p.proposals import DecisionChangeProposal
from app.services.proposal_service import ProposalStore


log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class CompoundingLedger:
    """Aggregate durable proposal, outcome, and scorer observations.

    The ledger deliberately does not manufacture historical values. Proposal
    and outcome data come from ``ProposalStore``; IKS and conservation history
    are recorded only when a caller supplies a live observation.
    """

    def __init__(
        self,
        proposal_store: ProposalStore,
        db_path: str = ":memory:",
        *,
        iks_provider: Callable[[], Mapping[str, Any] | float | None] | None = None,
        conservation_provider: Callable[[], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self.proposal_store = proposal_store
        self.iks_provider = iks_provider
        self.conservation_provider = conservation_provider
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = RLock()
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS iks_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    iks_value REAL NOT NULL,
                    source TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conservation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    phase TEXT,
                    alpha REAL,
                    q REAL,
                    status TEXT,
                    metadata TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS governance_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    category TEXT,
                    payload TEXT NOT NULL
                )"""
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def record_iks(
        self,
        iks_value: float,
        *,
        observed_at: str | None = None,
        source: str = "scorer",
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        value = _finite(iks_value)
        if value is None:
            raise ValueError("iks_value must be finite")
        point = {
            "timestamp": observed_at or _now_iso(),
            "iks_value": value,
            "source": source,
            "metadata": _json_safe(dict(metadata or {})),
        }
        with self._lock:
            self._connection.execute(
                "INSERT INTO iks_history (observed_at, iks_value, source, metadata) VALUES (?, ?, ?, ?)",
                (point["timestamp"], value, source, json.dumps(point["metadata"], sort_keys=True)),
            )
            self._connection.commit()
        return point

    def record_conservation(
        self,
        state: Mapping[str, Any],
        *,
        observed_at: str | None = None,
        source: str = "scorer",
    ) -> dict[str, Any]:
        payload = dict(state)
        phase = payload.get("phase")
        status = payload.get("status") or payload.get("state")
        alpha = _finite(payload.get("alpha"))
        q = _finite(payload.get("q"))
        if q is None:
            verified = _finite(payload.get("verified_count"))
            correct = _finite(payload.get("correct_count"))
            if verified is not None and verified > 0 and correct is not None:
                q = max(0.0, min(correct / verified, 1.0))
        point = {
            "timestamp": observed_at or _now_iso(),
            "phase": None if phase is None else str(phase),
            "alpha": alpha,
            "q": q,
            "status": None if status is None else str(status),
            "source": source,
            "metadata": _json_safe(payload),
        }
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO conservation_history
                    (observed_at, phase, alpha, q, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    point["timestamp"],
                    point["phase"],
                    point["alpha"],
                    point["q"],
                    point["status"],
                    json.dumps(point["metadata"], sort_keys=True),
                ),
            )
            self._connection.commit()
        return point

    def refresh_live_observations(self) -> None:
        """Capture current scorer observations when live providers are configured."""
        if self.iks_provider is not None:
            try:
                value = self.iks_provider()
            except Exception:
                log.exception("Unable to capture live S2P IKS observation")
                value = None
            if isinstance(value, Mapping):
                iks = _finite(value.get("iks_value", value.get("iks")))
                if iks is not None:
                    self.record_iks(
                        iks,
                        observed_at=str(value.get("timestamp") or _now_iso()),
                        source=str(value.get("source") or "scorer"),
                        metadata=value,
                    )
            elif value is not None:
                iks = _finite(value)
                if iks is not None:
                    self.record_iks(iks)
        if self.conservation_provider is not None:
            try:
                state = self.conservation_provider()
            except Exception:
                log.exception("Unable to capture live S2P conservation observation")
                state = None
            if state is not None:
                self.record_conservation(state)

    def timeline(self, limit: int = 100) -> list[dict[str, Any]]:
        proposals = self.proposal_store.list_recent(max(int(limit), 0))
        entries: list[dict[str, Any]] = []
        for proposal in proposals:
            entries.append(self._proposal_event(proposal))
            outcome = self.proposal_store.get_outcome(proposal.proposal_id)
            if outcome is not None and proposal.outcome_receipt_id:
                entries.append(self._outcome_event(proposal, outcome))
        entries.sort(key=lambda item: (str(item["timestamp"]), str(item.get("proposal_id", ""))), reverse=True)
        with self._lock:
            rows = self._connection.execute(
                "SELECT observed_at, event_type, category, payload FROM governance_events ORDER BY observed_at DESC, id DESC LIMIT ?",
                (max(int(limit), 0),),
            ).fetchall()
        for row in rows:
            payload = _json_safe(json.loads(str(row[3])))
            if isinstance(payload, dict):
                entries.append({"timestamp": str(row[0]), "event_type": str(row[1]), "category": row[2], **payload})
        entries.sort(key=lambda item: (str(item["timestamp"]), str(item.get("proposal_id", ""))), reverse=True)
        return entries[: max(int(limit), 0)]

    def record_governance_event(self, event_type: str, category: str | None, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = {"timestamp": _now_iso(), "event_type": event_type, "category": category, **_json_safe(dict(payload))}
        with self._lock:
            self._connection.execute(
                "INSERT INTO governance_events (observed_at, event_type, category, payload) VALUES (?, ?, ?, ?)",
                (event["timestamp"], event_type, category, json.dumps(_json_safe(dict(payload)), sort_keys=True)),
            )
            self._connection.commit()
        return event

    def summary(self) -> dict[str, Any]:
        proposals = self.proposal_store.list_recent(max(self.proposal_store.count(), 1))
        verified = []
        for proposal in proposals:
            outcome = self.proposal_store.get_outcome(proposal.proposal_id)
            if outcome is not None and proposal.outcome_receipt_id:
                verified.append((proposal, outcome))
        total_impact = 0.0
        by_category: dict[str, float] = {}
        savings_rates: list[float] = []
        correct = 0
        measured_impact_count = 0
        for proposal, outcome in verified:
            if bool(outcome.get("correct")):
                correct += 1
            impact = _impact_value(outcome.get("measured_impact"))
            if impact is not None:
                measured_impact_count += 1
                total_impact += impact
                by_category[proposal.category] = by_category.get(proposal.category, 0.0) + impact
            rate = _finite((outcome.get("measured_impact") or {}).get("savings_rate")) if isinstance(outcome.get("measured_impact"), Mapping) else None
            if rate is not None:
                savings_rates.append(rate)
        return {
            "total_decisions": len(proposals),
            "verified_outcomes": len(verified),
            "correct_outcomes": correct,
            "accuracy": (correct / len(verified)) if verified else None,
            "total_impact": total_impact,
            "per_category": by_category,
            "savings_rate": (sum(savings_rates) / len(savings_rates)) if savings_rates else None,
            "measured_impact_count": measured_impact_count,
            "evidence_tier": "T_O" if verified else "T_S",
        }

    def iks_trajectory(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT observed_at, iks_value, source, metadata FROM iks_history ORDER BY observed_at DESC, id DESC LIMIT ?",
                (max(int(limit), 0),),
            ).fetchall()
        return [
            {
                "timestamp": str(row[0]),
                "iks_value": float(row[1]),
                "source": str(row[2]),
                "metadata": _json_safe(json.loads(str(row[3]))),
                "evidence_tier": "T_O",
            }
            for row in reversed(rows)
        ]

    def conservation_history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT observed_at, phase, alpha, q, status, metadata
                FROM conservation_history ORDER BY observed_at DESC, id DESC LIMIT ?
                """,
                (max(int(limit), 0),),
            ).fetchall()
        return [
            {
                "timestamp": str(row[0]),
                "phase": row[1],
                "alpha": row[2],
                "q": row[3],
                "status": row[4],
                "metadata": _json_safe(json.loads(str(row[5]))),
                "evidence_tier": "T_O",
            }
            for row in reversed(rows)
        ]

    def _proposal_event(self, proposal: DecisionChangeProposal) -> dict[str, Any]:
        return {
            "timestamp": proposal.created_at,
            "event_type": "proposal",
            "source": "proposal_store",
            "proposal_id": proposal.proposal_id,
            "outcome_receipt_id": proposal.outcome_receipt_id,
            "category": proposal.category,
            "action": proposal.proposed_action,
            "confidence": proposal.confidence,
            "correct": None,
            "impact": None,
            "evidence_tier": "T_S",
            "evidence_label": "synthetic/modelled until verified",
        }

    def _outcome_event(self, proposal: DecisionChangeProposal, outcome: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": str(outcome.get("timestamp") or proposal.created_at),
            "event_type": "outcome",
            "source": "verified_outcome",
            "proposal_id": proposal.proposal_id,
            "outcome_receipt_id": proposal.outcome_receipt_id,
            "category": proposal.category,
            "action": proposal.proposed_action,
            "confidence": proposal.confidence,
            "correct": outcome.get("correct"),
            "impact": _impact_value(outcome.get("measured_impact")),
            "evidence_tier": "T_O",
            "evidence_label": "observed/measured",
        }


def _impact_value(value: Any) -> float | None:
    if isinstance(value, Mapping):
        for key in ("total_impact", "financial_impact", "savings_usd", "dollars_saved", "savings", "impact"):
            candidate = _finite(value.get(key))
            if candidate is not None:
                return candidate
        return None
    return _finite(value)
