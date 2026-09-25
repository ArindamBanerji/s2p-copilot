from __future__ import annotations

from app.routers.s2p import ACTION_CATEGORY_MAP


def test_auto_approve_maps_to_approve() -> None:
    assert ACTION_CATEGORY_MAP["auto_approve"] == "APPROVE"


def test_refer_to_specialist_maps_to_hold() -> None:
    assert ACTION_CATEGORY_MAP["refer_to_specialist"] == "HOLD"


def test_hold_for_review_maps_to_hold() -> None:
    assert ACTION_CATEGORY_MAP["hold_for_review"] == "HOLD"


def test_escalate_maps_to_escalate() -> None:
    assert ACTION_CATEGORY_MAP["escalate_compliance"] == "ESCALATE"


def test_flag_leakage_maps_to_escalate() -> None:
    assert ACTION_CATEGORY_MAP["flag_leakage"] == "ESCALATE"


def test_unknown_action_maps_to_review() -> None:
    assert ACTION_CATEGORY_MAP.get("unknown_action", "REVIEW") == "REVIEW"
