"""S2P reward function for invoice exception triage."""

from __future__ import annotations

from typing import Any, Mapping

from copilot_sdk.rl import RewardComputer
from app.domains.s2p.config import S2PDomainConfig


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


class _S2PRawRewardFunction:
    """Calculate the S2P domain formula before SDK normalization."""

    def compute(
        self,
        recommended_action: str,
        actual_action: str,
        outcome: Mapping[str, Any],
    ) -> float:
        accuracy_value = outcome.get("exception_accuracy", outcome.get("accuracy"))
        if accuracy_value is None:
            accuracy = 1.0 if actual_action == recommended_action else 0.0
        else:
            accuracy = _clamp(_number(accuracy_value, 0.0), 0.0, 1.0)

        savings_ratio = _savings_ratio(outcome, default=1.0 if accuracy > 0 else 0.0)
        return _clamp(accuracy * savings_ratio, 0.0, 1.0)


class S2PGradedRewardFunction:
    """SDK-compatible graded reward for invoice exception decisions.

    The scorer in the frozen SDK uses the legacy action-oriented hook. This
    class keeps that hook while routing normalization through the SDK's
    ``RewardComputer``. The domain formula itself is strictly in [0, 1].
    """

    name = "s2p_graded_financial"

    def __init__(self) -> None:
        self._computer = RewardComputer(
            _S2PRawRewardFunction(), domain="s2p"
        )

    def compute(
        self,
        recommended_action: str,
        actual_action: str,
        outcome: Mapping[str, Any],
    ) -> float:
        result = self._computer.compute(
            recommended_action,
            actual_action,
            outcome,
        )
        return result.reward

    def compute_reward(
        self,
        decision: Mapping[str, Any],
        outcome: Mapping[str, Any],
    ) -> float:
        """Expose the v1 mapping-shaped API for direct SDK RL callers."""
        factor_vector = decision.get("factor_vector")
        if factor_vector is not None:
            try:
                factor_count = len(factor_vector)
            except TypeError as exc:
                raise ValueError("factor_vector must be a sized sequence") from exc
            if factor_count != S2PDomainConfig.n_factors:
                raise ValueError(
                    f"S2P factor_vector must contain {S2PDomainConfig.n_factors} factors; "
                    f"received {factor_count}"
                )
        recommended_action = str(
            decision.get("recommended_action", decision.get("action", ""))
        )
        actual_action = str(outcome.get("actual_action", outcome.get("action", "")))
        return self.compute(recommended_action, actual_action, outcome)

    def reward_range(self) -> tuple[float, float]:
        return (0.0, 1.0)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _savings_ratio(outcome: Mapping[str, Any], default: float) -> float:
    explicit_ratio = outcome.get("savings_ratio")
    if explicit_ratio is not None:
        return _clamp(_number(explicit_ratio, default), 0.0, 1.0)

    recovered = outcome.get(
        "recovered_value",
        outcome.get("amount_recovered", outcome.get("recovery")),
    )
    at_risk = outcome.get(
        "amount_at_risk",
        outcome.get("at_risk", outcome.get("amount")),
    )
    if recovered is not None and at_risk is not None:
        risk_value = _number(at_risk, 0.0)
        if risk_value > 0.0:
            return _clamp(_number(recovered, 0.0) / risk_value, 0.0, 1.0)

    recovery_pct = outcome.get("recovery_pct")
    if recovery_pct is not None:
        return _clamp(_number(recovery_pct, default * 100.0) / 100.0, 0.0, 1.0)
    return _clamp(default, 0.0, 1.0)


def _clamp(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return float(max(lower, min(float(value), upper)))
