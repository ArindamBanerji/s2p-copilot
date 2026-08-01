from __future__ import annotations

import pytest
from gae.calibration import conservation_status

from copilot_sdk.graph.memory_store import InMemoryGraphStore
from app.services.s2p_auto_approve_gate import AutoApproveConfig, AutoApproveGate


def test_conservation_green_requires_theta_min_met() -> None:
    verified = 100
    correct = 90
    total_decisions = 100
    alpha = verified / total_decisions
    q = correct / verified

    result = conservation_status(
        verified_count=verified,
        correct_count=correct,
        total_decisions=total_decisions,
        penalty_ratio=5.0,
    )

    assert result.signal == pytest.approx(alpha * q * verified)
    assert result.signal >= 2 * result.theta_min
    assert result.status == "GREEN"


def test_conservation_red_when_below_threshold() -> None:
    result = conservation_status(
        verified_count=1,
        correct_count=0,
        total_decisions=100,
        penalty_ratio=5.0,
    )

    assert result.signal < result.theta_min
    assert result.status != "GREEN"


def test_auto_approve_gate_rejects_non_green() -> None:
    store = InMemoryGraphStore(domain="s2p")
    gate = AutoApproveGate(
        AutoApproveConfig(
            enabled=True,
            mode="shadow",
            min_verified_decisions=0,
            spot_check_rate=0.0,
        )
    )

    result = gate.evaluate(
        category="price_variance",
        confidence=0.99,
        recommended_action="auto_approve",
        graph_store=store,
        conservation_status="RED",
    )

    assert result["would_auto_approve"] is False
    assert result["blocked_reason"] == "conservation_not_green"
