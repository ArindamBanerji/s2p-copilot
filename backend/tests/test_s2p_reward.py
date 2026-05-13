from __future__ import annotations

import inspect

from app.domains.s2p.reward import S2PRewardFunction


def test_confirm_full_recovery() -> None:
    reward = S2PRewardFunction().compute(
        "auto_approve",
        "auto_approve",
        {"recovery_pct": 100},
    )

    assert reward == 1.0


def test_confirm_partial_recovery() -> None:
    reward = S2PRewardFunction().compute(
        "hold_for_review",
        "hold_for_review",
        {"recovery_pct": 35},
    )

    assert reward == 0.35


def test_confirm_no_context() -> None:
    reward = S2PRewardFunction().compute("flag_leakage", "flag_leakage", {})

    assert reward == 1.0


def test_override_full_risk() -> None:
    reward = S2PRewardFunction().compute(
        "auto_approve",
        "hold_for_review",
        {"amount": 1000, "at_risk": 1000},
    )

    assert reward == -1.0


def test_override_partial_risk() -> None:
    reward = S2PRewardFunction().compute(
        "auto_approve",
        "hold_for_review",
        {"amount": 1000, "at_risk": 250},
    )

    assert reward == -0.25


def test_override_no_context() -> None:
    reward = S2PRewardFunction().compute("auto_approve", "hold_for_review", {})

    assert reward == -1.0


def test_override_zero_amount() -> None:
    reward = S2PRewardFunction().compute(
        "auto_approve",
        "hold_for_review",
        {"amount": 0, "at_risk": 10},
    )

    assert reward == -1.0


def test_reward_clamped() -> None:
    fn = S2PRewardFunction()

    assert fn.compute("auto_approve", "auto_approve", {"recovery_pct": 250}) == 1.0
    assert fn.compute("auto_approve", "hold_for_review", {"amount": 100, "at_risk": 400}) == -1.0


def test_reward_function_signature_matches_sdk_protocol() -> None:
    signature = inspect.signature(S2PRewardFunction.compute)
    assert list(signature.parameters) == [
        "self",
        "recommended_action",
        "actual_action",
        "outcome",
    ]


def test_argument_order_override_is_negative() -> None:
    reward = S2PRewardFunction().compute(
        recommended_action="auto_approve",
        actual_action="hold_for_review",
        outcome={"amount": 1000, "at_risk": 100},
    )

    assert reward < 0
