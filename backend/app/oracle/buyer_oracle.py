"""Parametric buyer oracle for S2P measurement-pipeline validation."""

from __future__ import annotations

import random
from typing import Any


class BuyerOracle:
    """Generate synthetic buyer outcomes with a known injected effect.

    The oracle validates that the measurement pipeline detects the injected
    treatment effect. The `correct` field is modeled, not customer-measured.
    """

    def __init__(
        self,
        *,
        base_hold_rate: float = 0.40,
        treatment_lift: float = 0.08,
        base_accuracy: float = 0.65,
        accuracy_lift: float = 0.05,
        seed: int = 42,
    ):
        self._base_rate = base_hold_rate
        self._lift = treatment_lift
        self._base_accuracy = base_accuracy
        self._accuracy_lift = accuracy_lift
        self._rng = random.Random(seed)

    def synthetic_outcome(self, *, shown: bool) -> dict[str, Any]:
        """Generate one synthetic buyer outcome.

        Returns: {buyer_action, was_override, quality_signal, correct}
        `correct` is MODELED (META-4 line).
        """
        p_hold = self._base_rate + (self._lift if shown else 0.0)
        hold = self._rng.random() < p_hold

        p_correct = self._base_accuracy + (self._accuracy_lift if shown else 0.0)
        correct = self._rng.random() < p_correct

        if hold:
            action = "hold_for_review"
        elif self._rng.random() < 0.10:
            action = "escalate"
        else:
            action = "auto_approve"

        return {
            "buyer_action": action,
            "was_override": self._rng.random() < 0.12,
            "quality_signal": 1.0 if correct else 0.0,
            "correct": correct,
        }
