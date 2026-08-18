"""Persistence and lifecycle service for S2P Decision-Change Proposals."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from threading import RLock
from typing import Any, Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5

from copilot_sdk.outcome.models import VerifiedOutcome

from app.domains.s2p.proposals import DecisionChangeProposal


class OutcomeAdapter(Protocol):
    def record(self, outcome: VerifiedOutcome) -> str:
        ...


class LocalOutcomeAdapter:
    """Adapter that records canonical SDK outcomes by stable receipt ID."""

    def __init__(self) -> None:
        self._outcomes: dict[str, VerifiedOutcome] = {}
        self._lock = RLock()

    def record(self, outcome: VerifiedOutcome) -> str:
        receipt_id = str(outcome.receipt_id())
        with self._lock:
            self._outcomes.setdefault(receipt_id, outcome)
        return receipt_id

    def get(self, receipt_id: str) -> VerifiedOutcome | None:
        with self._lock:
            return self._outcomes.get(receipt_id)


class ProposalStore:
    """Restart-safe SQLite store for proposals and canonical outcomes."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._connection = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = RLock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_change_proposals (
                proposal_id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL,
                copilot TEXT NOT NULL,
                category TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                proposed_action TEXT NOT NULL,
                confidence REAL NOT NULL,
                factor_vector TEXT NOT NULL,
                evidence_chain TEXT NOT NULL,
                similar_decisions TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                outcome_receipt_id TEXT,
                expected_kpi_delta TEXT,
                rollback_path TEXT,
                outcome_payload TEXT
            )
            """
        )
        self._connection.commit()

    def save(self, proposal: DecisionChangeProposal) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO decision_change_proposals (
                    proposal_id, invoice_id, copilot, category, decision_id,
                    proposed_action, confidence, factor_vector, evidence_chain,
                    similar_decisions, created_at, status, outcome_receipt_id,
                    expected_kpi_delta, rollback_path, outcome_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                    invoice_id=excluded.invoice_id,
                    copilot=excluded.copilot,
                    category=excluded.category,
                    decision_id=excluded.decision_id,
                    proposed_action=excluded.proposed_action,
                    confidence=excluded.confidence,
                    factor_vector=excluded.factor_vector,
                    evidence_chain=excluded.evidence_chain,
                    similar_decisions=excluded.similar_decisions,
                    created_at=excluded.created_at,
                    status=excluded.status,
                    outcome_receipt_id=excluded.outcome_receipt_id,
                    expected_kpi_delta=excluded.expected_kpi_delta,
                    rollback_path=excluded.rollback_path,
                    outcome_payload=COALESCE(excluded.outcome_payload, decision_change_proposals.outcome_payload)
                """,
                self._parameters(proposal, self._outcome_payload(proposal.proposal_id)),
            )
            self._connection.commit()

    def get(self, proposal_id: str) -> DecisionChangeProposal | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM decision_change_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return _row_to_proposal(row) if row else None

    def get_by_invoice(self, invoice_id: str) -> list[DecisionChangeProposal]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM decision_change_proposals WHERE invoice_id = ? ORDER BY created_at DESC",
                (invoice_id,),
            ).fetchall()
        return [_row_to_proposal(row) for row in rows]

    def list_recent(self, limit: int = 50) -> list[DecisionChangeProposal]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM decision_change_proposals ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (max(int(limit), 0),),
            ).fetchall()
        return [_row_to_proposal(row) for row in rows]

    def link_outcome(
        self,
        proposal_id: str,
        receipt_id: str,
        outcome_payload: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE decision_change_proposals
                SET outcome_receipt_id = ?, outcome_payload = ?
                WHERE proposal_id = ?
                """,
                (
                    receipt_id,
                    None if outcome_payload is None else json.dumps(dict(outcome_payload), sort_keys=True),
                    proposal_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown proposal_id: {proposal_id}")
            self._connection.commit()

    def count(self, status: str | None = None) -> int:
        with self._lock:
            if status is None:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM decision_change_proposals"
                ).fetchone()
            else:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM decision_change_proposals WHERE status = ?",
                    (status,),
                ).fetchone()
        return int(row[0]) if row else 0

    def get_outcome(self, proposal_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT outcome_payload FROM decision_change_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return dict(json.loads(str(row[0])))

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _parameters(self, proposal: DecisionChangeProposal, outcome_payload: str | None) -> tuple[Any, ...]:
        return (
            proposal.proposal_id,
            proposal.invoice_id,
            proposal.copilot,
            proposal.category,
            proposal.decision_id,
            proposal.proposed_action,
            proposal.confidence,
            json.dumps(proposal.factor_vector),
            json.dumps(proposal.evidence_chain, sort_keys=True),
            json.dumps(proposal.similar_decisions),
            proposal.created_at,
            proposal.status,
            proposal.outcome_receipt_id,
            None if proposal.expected_kpi_delta is None else json.dumps(proposal.expected_kpi_delta, sort_keys=True),
            None if proposal.rollback_path is None else json.dumps(proposal.rollback_path, sort_keys=True),
            outcome_payload,
        )

    def _outcome_payload(self, proposal_id: str) -> str | None:
        existing = self.get_outcome(proposal_id)
        return None if existing is None else json.dumps(existing, sort_keys=True)


class ProposalService:
    """Create proposals and close their human-verification outcome loop."""

    def __init__(
        self,
        store: ProposalStore | None = None,
        outcome_adapter: OutcomeAdapter | None = None,
    ) -> None:
        self.store = store or ProposalStore()
        self.outcome_adapter = outcome_adapter or LocalOutcomeAdapter()
        self._lock = RLock()

    def create_from_score(
        self,
        score_result: Any,
        invoice_id: str,
        factor_vector: list[float],
        evidence: Mapping[str, Any] | list[dict[str, Any]],
    ) -> DecisionChangeProposal:
        if not invoice_id.strip():
            raise ValueError("invoice_id is required")
        score = _as_mapping(score_result)
        evidence_map = evidence if isinstance(evidence, Mapping) else {}
        chain = evidence_map.get("evidence_chain", evidence if isinstance(evidence, list) else [])
        if not isinstance(chain, list):
            raise ValueError("evidence_chain must be a list")
        created_at = _now_iso()
        decision_id = str(score.get("decision_id") or invoice_id)
        proposal = DecisionChangeProposal(
            proposal_id=str(uuid5(NAMESPACE_URL, f"s2p:proposal:{invoice_id}:{created_at}")),
            copilot="s2p",
            invoice_id=invoice_id,
            category=str(score.get("category") or evidence_map.get("category") or "unknown"),
            decision_id=decision_id,
            proposed_action=str(score.get("action") or score.get("proposed_action") or ""),
            confidence=float(score.get("confidence", 0.0)),
            factor_vector=list(factor_vector),
            evidence_chain=[dict(item) for item in chain],
            similar_decisions=[str(item) for item in evidence_map.get("similar_decisions", [])],
            created_at=created_at,
            expected_kpi_delta=_optional_dict(evidence_map.get("expected_kpi_delta")),
            rollback_path=evidence_map.get("rollback_path"),
        )
        with self._lock:
            self.store.save(proposal)
        return proposal

    def confirm(self, proposal_id: str) -> DecisionChangeProposal:
        return self._resolve(proposal_id, "confirmed", None, None)

    def override(self, proposal_id: str, override_action: str, reason: str) -> DecisionChangeProposal:
        if not override_action.strip() or not reason.strip():
            raise ValueError("override_action and reason are required")
        return self._resolve(proposal_id, "overridden", override_action, reason)

    def link_outcome(self, proposal_id: str, receipt_id: str) -> DecisionChangeProposal:
        proposal = self._required(proposal_id)
        self.store.link_outcome(proposal_id, receipt_id)
        updated = DecisionChangeProposal.from_dict({**proposal.to_dict(), "outcome_receipt_id": receipt_id})
        self.store.save(updated)
        return updated

    def get_audit_trail(self, invoice_id: str) -> list[dict[str, Any]]:
        return [
            {
                "proposal": proposal.to_dict(),
                "outcome": self.store.get_outcome(proposal.proposal_id),
            }
            for proposal in self.store.get_by_invoice(invoice_id)
        ]

    def _resolve(
        self,
        proposal_id: str,
        status: str,
        override_action: str | None,
        reason: str | None,
    ) -> DecisionChangeProposal:
        with self._lock:
            proposal = self._required(proposal_id)
            if proposal.status == status:
                return proposal
            if proposal.status != "proposed":
                raise ValueError(f"proposal {proposal_id} is already {proposal.status}")
            outcome = VerifiedOutcome.create(
                copilot="s2p",
                decision_id=proposal.decision_id,
                category=proposal.category,
                factor_vector=proposal.factor_vector,
                predicted_action=proposal.proposed_action,
                human_disposition="override" if status == "overridden" else "confirm",
                override_action=override_action,
                override_reason=reason,
                correct=status == "confirmed",
                measured_impact=proposal.expected_kpi_delta,
                evidence_provenance=f"decision_change_proposal:{proposal.proposal_id}",
            )
            receipt_id = self.outcome_adapter.record(outcome)
            resolved = DecisionChangeProposal.from_dict(
                {
                    **proposal.to_dict(),
                    "status": status,
                    "outcome_receipt_id": receipt_id,
                }
            )
            self.store.save(resolved)
            self.store.link_outcome(proposal_id, receipt_id, outcome.to_dict())
            return resolved

    def _required(self, proposal_id: str) -> DecisionChangeProposal:
        proposal = self.store.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Unknown proposal_id: {proposal_id}")
        return proposal


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {
        key: getattr(value, key)
        for key in ("decision_id", "category", "action", "confidence")
        if hasattr(value, key)
    }


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return None if value is None else dict(value)


def _row_to_proposal(row: tuple[Any, ...]) -> DecisionChangeProposal:
    return DecisionChangeProposal(
        proposal_id=str(row[0]),
        invoice_id=str(row[1]),
        copilot=str(row[2]),
        category=str(row[3]),
        decision_id=str(row[4]),
        proposed_action=str(row[5]),
        confidence=float(row[6]),
        factor_vector=list(json.loads(str(row[7]))),
        evidence_chain=list(json.loads(str(row[8]))),
        similar_decisions=list(json.loads(str(row[9]))),
        created_at=str(row[10]),
        status=str(row[11]),
        outcome_receipt_id=None if row[12] is None else str(row[12]),
        expected_kpi_delta=None if row[13] is None else dict(json.loads(str(row[13]))),
        rollback_path=None if row[14] is None else json.loads(str(row[14])),
    )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()
