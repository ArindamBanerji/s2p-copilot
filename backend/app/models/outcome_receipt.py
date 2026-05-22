"""Outcome receipt model for S2P evidence chains."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Optional


@dataclass
class OutcomeReceipt:
    receipt_id: str
    invoice_id: str
    timestamp: str
    scored_action: str
    confidence: float
    factor_vector: list[float]
    category: str
    human_action: str
    override_reason: Optional[str] = None
    reward: float = 0.0
    centroid_updated: bool = False
    conservation_state_before: str = ""
    conservation_state_after: str = ""
    verified_count_before: int = 0
    verified_count_after: int = 0
    previous_receipt_hash: str = ""
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.confidence = float(self.confidence)
        self.reward = float(self.reward)
        self.factor_vector = [float(value) for value in self.factor_vector]
        self.verified_count_before = int(self.verified_count_before)
        self.verified_count_after = int(self.verified_count_after)
        self.receipt_hash = self.compute_hash()

    def compute_hash(self) -> str:
        stable_payload = {
            "receipt_id": self.receipt_id,
            "invoice_id": self.invoice_id,
            "timestamp": self.timestamp,
            "scored_action": self.scored_action,
            "confidence": round(self.confidence, 8),
            "factor_vector": [round(float(value), 8) for value in self.factor_vector],
            "category": self.category,
            "human_action": self.human_action,
            "override_reason": self.override_reason,
            "reward": round(self.reward, 8),
            "centroid_updated": bool(self.centroid_updated),
            "conservation_state_before": self.conservation_state_before,
            "conservation_state_after": self.conservation_state_after,
            "verified_count_before": self.verified_count_before,
            "verified_count_after": self.verified_count_after,
            "previous_receipt_hash": self.previous_receipt_hash,
        }
        encoded = json.dumps(stable_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "invoice_id": self.invoice_id,
            "timestamp": self.timestamp,
            "scored_action": self.scored_action,
            "confidence": round(self.confidence, 6),
            "factor_vector": [round(float(value), 6) for value in self.factor_vector],
            "category": self.category,
            "human_action": self.human_action,
            "override_reason": self.override_reason,
            "reward": round(self.reward, 6),
            "centroid_updated": self.centroid_updated,
            "conservation_state_before": self.conservation_state_before,
            "conservation_state_after": self.conservation_state_after,
            "verified_count_before": self.verified_count_before,
            "verified_count_after": self.verified_count_after,
            "previous_hash": self.previous_receipt_hash,
            "receipt_hash": self.receipt_hash,
        }
