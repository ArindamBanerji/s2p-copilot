"""S2P reward function for invoice exception triage."""

from __future__ import annotations

from typing import Any


class S2PRewardFunction:
    """Graded financial reward matching the SDK RewardFunction protocol."""

    name = "s2p_graded_financial"

    def compute(
        self,
        recommended_action: str,
        actual_action: str,
        outcome: dict[str, Any],
    ) -> float:
        if actual_action == recommended_action:
            recovery_pct = _number(outcome.get("recovery_pct"), 100.0)
            return _clamp(recovery_pct / 100.0, 0.0, 1.0)

        amount = _number(outcome.get("amount"), 0.0)
        at_risk = _number(outcome.get("at_risk"), amount)
        if amount > 0:
            return _clamp(-min(1.0, at_risk / amount), -1.0, 1.0)
        return -1.0


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return float(max(lower, min(float(value), upper)))
