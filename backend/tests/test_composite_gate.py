from __future__ import annotations

import inspect

import pytest

from app.domains.s2p.config import S2P_ACTIONS, S2P_CATEGORIES
from app.framework.composite_gate import CompositeDiscriminant


S2P_THRESHOLDS = {
    "price_variance": 0.85,
    "quantity_mismatch": 0.82,
    "duplicate_risk": 0.90,
    "contract_gap": 0.80,
    "format_compliance": 0.88,
}

SOC_CATEGORIES = {
    "credential_access",
    "malware_execution",
    "data_exfiltration",
    "lateral_movement",
    "privilege_escalation",
}

SOC_ACTIONS = {
    "suppress",
    "monitor",
    "refer_to_analyst",
}


def test_composite_gate_uses_s2p_categories() -> None:
    assert set(CompositeDiscriminant.CATEGORY_CONFIDENCE_THRESHOLDS) == set(S2P_CATEGORIES)


def test_composite_gate_no_soc_categories() -> None:
    source = inspect.getsource(CompositeDiscriminant)

    assert SOC_CATEGORIES.isdisjoint(CompositeDiscriminant.CATEGORY_CONFIDENCE_THRESHOLDS)
    for term in SOC_CATEGORIES:
        assert term not in source


def test_composite_gate_auto_approve_protected() -> None:
    signature = inspect.signature(CompositeDiscriminant.evaluate)

    assert signature.parameters["protected_action_name"].default == "auto_approve"
    assert "auto_approve" in S2P_ACTIONS
    assert SOC_ACTIONS.isdisjoint(S2P_ACTIONS)


def test_composite_gate_threshold_per_category() -> None:
    assert CompositeDiscriminant.CATEGORY_CONFIDENCE_THRESHOLDS == pytest.approx(
        S2P_THRESHOLDS
    )


def test_composite_gate_with_s2p_scorer() -> None:
    assert CompositeDiscriminant.CATEGORY_CONFIDENCE_THRESHOLDS["duplicate_risk"] == 0.90
    assert S2P_ACTIONS[0] == "auto_approve"
