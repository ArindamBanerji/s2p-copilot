"""Canonical S2P Decision-Change Proposal value object."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Mapping


PROPOSAL_STATUSES = frozenset({"proposed", "confirmed", "overridden", "expired"})


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass(frozen=True)
class DecisionChangeProposal:
    """One auditable proposed decision change and its evidence chain."""

    proposal_id: str
    copilot: str
    invoice_id: str
    category: str
    decision_id: str
    proposed_action: str
    confidence: float
    factor_vector: list[float]
    evidence_chain: list[dict[str, Any]]
    similar_decisions: list[str]
    created_at: str
    status: str = "proposed"
    outcome_receipt_id: str | None = None
    expected_kpi_delta: dict[str, Any] | None = None
    rollback_path: dict[str, Any] | str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "proposal_id",
            "copilot",
            "invoice_id",
            "category",
            "decision_id",
            "proposed_action",
            "created_at",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.status not in PROPOSAL_STATUSES:
            raise ValueError(f"status must be one of {sorted(PROPOSAL_STATUSES)}")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be finite and between 0 and 1")
        vector = [float(value) for value in self.factor_vector]
        if not vector or not all(math.isfinite(value) for value in vector):
            raise ValueError("factor_vector must contain finite numeric values")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "factor_vector", vector)
        object.__setattr__(self, "evidence_chain", [dict(item) for item in self.evidence_chain])
        object.__setattr__(self, "similar_decisions", [str(item) for item in self.similar_decisions])

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "copilot": self.copilot,
            "invoice_id": self.invoice_id,
            "category": self.category,
            "decision_id": self.decision_id,
            "proposed_action": self.proposed_action,
            "confidence": self.confidence,
            "factor_vector": list(self.factor_vector),
            "evidence_chain": [dict(item) for item in self.evidence_chain],
            "similar_decisions": list(self.similar_decisions),
            "created_at": self.created_at,
            "status": self.status,
            "outcome_receipt_id": self.outcome_receipt_id,
            "expected_kpi_delta": None if self.expected_kpi_delta is None else dict(self.expected_kpi_delta),
            "rollback_path": self.rollback_path,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DecisionChangeProposal":
        return cls(
            proposal_id=str(value["proposal_id"]),
            copilot=str(value.get("copilot", "s2p")),
            invoice_id=str(value["invoice_id"]),
            category=str(value["category"]),
            decision_id=str(value["decision_id"]),
            proposed_action=str(value["proposed_action"]),
            confidence=float(value["confidence"]),
            factor_vector=list(value["factor_vector"]),
            evidence_chain=list(value.get("evidence_chain", [])),
            similar_decisions=list(value.get("similar_decisions", [])),
            created_at=str(value["created_at"]),
            status=str(value.get("status", "proposed")),
            outcome_receipt_id=(
                None if value.get("outcome_receipt_id") is None else str(value["outcome_receipt_id"])
            ),
            expected_kpi_delta=(
                None if value.get("expected_kpi_delta") is None else dict(value["expected_kpi_delta"])
            ),
            rollback_path=value.get("rollback_path"),
        )
