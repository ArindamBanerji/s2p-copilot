"""Parametric buyer oracle for S2P measurement-pipeline validation."""

from __future__ import annotations

import random
from typing import Any


class _BuyerOutcome(dict):
    def __iter__(self):
        return (key for key in super().__iter__() if key != "action")

    def __getitem__(self, key: str) -> Any:
        if key == "action":
            return super().__getitem__("buyer_action")
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "action":
            return super().get("buyer_action", default)
        return super().get(key, default)


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

    @property
    def known_effect(self) -> float:
        """Known treatment lift on hold rate."""

        return self._lift

    @property
    def known_accuracy_effect(self) -> float:
        """Known treatment lift on accuracy."""

        return self._accuracy_lift

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

        return _BuyerOutcome(
            {
                "action": action,
                "buyer_action": action,
                "was_override": self._rng.random() < 0.12,
                "quality_signal": 1.0 if correct else 0.0,
                "correct": correct,
            }
        )
