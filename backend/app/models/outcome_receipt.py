"""Outcome receipt model for S2P evidence chains."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Optional


@dataclass
class OutcomeReceipt:
    receipt_id: str
    invoice_id: str
    timestamp: str
    scored_action: str
    confidence: float
    factor_vector: list[float]
    category: str | None
    human_action: str
    decision_id: str | None = None
    recommended_action: str | None = None
    actual_action: str | None = None
    is_correct: bool | None = None
    factors: dict[str, Any] | None = None
    conservation_status: str = "UNKNOWN"
    amount: float | None = None
    amount_at_risk: float | None = None
    override_reason: Optional[str] = None
    reward: float | None = 0.0
    centroid_updated: bool = False
    conservation_state_before: str = ""
    conservation_state_after: str = ""
    verified_count_before: int = 0
    verified_count_after: int = 0
    previous_receipt_hash: str = ""
    receipt_hash: str = field(init=False)
    # PD v1.3 §8.2 — audit completeness fields
    amount_recovered: float | None = None
    supplier_name: str | None = None
    invoice_number: str | None = None
    po_number: str | None = None

    def __post_init__(self) -> None:
        self.confidence = float(self.confidence)
        self.reward = float(self.reward or 0.0)
        self.factor_vector = [float(value) for value in self.factor_vector]
        self.recommended_action = self.recommended_action or self.scored_action
        self.actual_action = self.actual_action or self.human_action
        if self.is_correct is None:
            self.is_correct = self.actual_action == self.recommended_action
        if self.factors is None:
            self.factors = {f"factor_{index}": value for index, value in enumerate(self.factor_vector)}
        else:
            self.factors = {str(key): float(value) for key, value in self.factors.items()}
        self.conservation_status = self.conservation_status or self.conservation_state_after or "UNKNOWN"
        self.amount = float(self.amount) if self.amount is not None else None
        self.amount_at_risk = float(self.amount_at_risk) if self.amount_at_risk is not None else None
        self.amount_recovered = float(self.amount_recovered) if self.amount_recovered is not None else None
        self.verified_count_before = int(self.verified_count_before)
        self.verified_count_after = int(self.verified_count_after)
        self.receipt_hash = self.compute_hash()

    def compute_hash(self) -> str:
        stable_payload = {
            "receipt_id": self.receipt_id,
            "invoice_id": self.invoice_id,
            "timestamp": self.timestamp,
            "scored_action": self.scored_action,
            "decision_id": self.decision_id,
            "recommended_action": self.recommended_action,
            "actual_action": self.actual_action,
            "is_correct": bool(self.is_correct),
            "confidence": round(self.confidence, 8),
            "factors": {
                key: round(float(value), 8)
                for key, value in sorted((self.factors or {}).items())
            },
            "factor_vector": [round(float(value), 8) for value in self.factor_vector],
            "category": self.category,
            "human_action": self.human_action,
            "conservation_status": self.conservation_status,
            "amount": round(self.amount, 8) if self.amount is not None else None,
            "amount_at_risk": round(self.amount_at_risk, 8) if self.amount_at_risk is not None else None,
            "override_reason": self.override_reason,
            "reward": round(self.reward, 8),
            "centroid_updated": bool(self.centroid_updated),
            "conservation_state_before": self.conservation_state_before,
            "conservation_state_after": self.conservation_state_after,
            "verified_count_before": self.verified_count_before,
            "verified_count_after": self.verified_count_after,
            "previous_receipt_hash": self.previous_receipt_hash,
            "amount_recovered": round(self.amount_recovered, 8) if self.amount_recovered is not None else None,
            "supplier_name": self.supplier_name,
            "invoice_number": self.invoice_number,
            "po_number": self.po_number,
        }
        encoded = json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "invoice_id": self.invoice_id,
            "timestamp": self.timestamp,
            "scored_action": self.scored_action,
            "decision_id": self.decision_id,
            "recommended_action": self.recommended_action,
            "actual_action": self.actual_action,
            "is_correct": self.is_correct,
            "confidence": round(self.confidence, 6),
            "factors": {
                key: round(float(value), 6)
                for key, value in sorted((self.factors or {}).items())
            },
            "factor_vector": [round(float(value), 6) for value in self.factor_vector],
            "category": self.category,
            "human_action": self.human_action,
            "conservation_status": self.conservation_status,
            "amount": round(self.amount, 6) if self.amount is not None else None,
            "amount_at_risk": round(self.amount_at_risk, 6) if self.amount_at_risk is not None else None,
            "override_reason": self.override_reason,
            "reward": round(self.reward, 6),
            "centroid_updated": self.centroid_updated,
            "conservation_state_before": self.conservation_state_before,
            "conservation_state_after": self.conservation_state_after,
            "verified_count_before": self.verified_count_before,
            "verified_count_after": self.verified_count_after,
            "previous_hash": self.previous_receipt_hash,
            "receipt_hash": self.receipt_hash,
            "amount_recovered": round(self.amount_recovered, 6) if self.amount_recovered is not None else None,
            "supplier_name": self.supplier_name,
            "invoice_number": self.invoice_number,
            "po_number": self.po_number,
        }
