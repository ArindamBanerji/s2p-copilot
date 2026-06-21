import asyncio
from pathlib import Path

from app.framework.intervention_controls import (
    ConservationStateMachine,
    InterventionControls,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class FakeDb:
    def __init__(self):
        self.queries = []

    async def run_query(self, query, params=None):
        self.queries.append((query, params or {}))
        return []


class FakeScorer:
    def __init__(self):
        self.frozen = False

    def freeze(self):
        self.frozen = True

    def unfreeze(self):
        self.frozen = False


class FakeGate:
    CONFIDENCE_THRESHOLD = 0.75
    CATEGORY_CONFIDENCE_THRESHOLDS = {}
    FROZEN_CATEGORIES = set()
    FORCE_REVIEW_CATEGORIES = set()
    AUTO_APPROVE_DISABLED = False


def make_controls():
    return InterventionControls(
        db_client=FakeDb(),
        scorer=FakeScorer(),
        checkpoint_service=None,
        composite_gate=FakeGate(),
    )


def test_intervention_controls_green_allows_learning():
    machine = ConservationStateMachine(initial_state="GREEN")

    decision = machine.learning_decision()

    assert decision["state"] == "GREEN"
    assert decision["learning_allowed"] is True
    assert decision["learning_paused"] is False
    assert decision["learning_blocked"] is False
    assert decision["protected_action"] == "auto_approve"
    assert decision["penalty_ratio"] == 5.0


def test_intervention_controls_amber_pauses_learning():
    machine = ConservationStateMachine(initial_state="GREEN")

    decision = machine.set_state(
        "AMBER",
        initiated_by="test",
        reason="verified correctness degraded",
    )

    assert decision["state"] == "AMBER"
    assert decision["learning_allowed"] is False
    assert decision["learning_paused"] is True
    assert decision["learning_blocked"] is False
    assert decision["transition_reason"] == "verified correctness degraded"


def test_intervention_controls_red_blocks_learning():
    machine = ConservationStateMachine(initial_state="GREEN")

    decision = machine.set_state(
        "RED",
        initiated_by="test",
        reason="protected action unsafe",
    )

    assert decision["state"] == "RED"
    assert decision["learning_allowed"] is False
    assert decision["learning_paused"] is True
    assert decision["learning_blocked"] is True
    assert decision["reason"] == "conservation_red_learning_blocked"


def test_intervention_controls_set_conservation_state_freezes_learning():
    controls = make_controls()

    result = asyncio.run(
        controls.set_conservation_state(
            "AMBER",
            initiated_by="operator",
            reason="pause while conservation recovers",
        )
    )

    assert controls.scorer.frozen is True
    assert result["type"] == "conservation_state"
    assert result["details"]["state"] == "AMBER"
    assert result["details"]["learning_paused"] is True
    assert result["details"]["protected_action"] == "auto_approve"


def test_intervention_controls_green_unfreezes_learning():
    controls = make_controls()
    controls.scorer.freeze()

    result = asyncio.run(
        controls.set_conservation_state(
            "GREEN",
            initiated_by="operator",
            reason="conservation recovered",
        )
    )

    assert controls.scorer.frozen is False
    assert result["details"]["state"] == "GREEN"
    assert result["details"]["learning_allowed"] is True


def test_intervention_controls_no_soc_action_vocabulary():
    source = (BACKEND_ROOT / "app" / "framework" / "intervention_controls.py").read_text(encoding="utf-8")
    forbidden = [
        "credential_access",
        "malware_execution",
        "data_exfiltration",
        "lateral_movement",
        "privilege_escalation",
        "suppress",
        "refer_to_analyst",
    ]

    assert [term for term in forbidden if term in source] == []
