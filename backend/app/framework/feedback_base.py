"""
Feedback trust/reward mechanics for CopilotFramework.
Domain-agnostic — no SOC references.
Safe to copy to copilot-sdk.

Provides:
  TRUST_SCORES, TRUST_HISTORY, LOW_TRUST_FLAGS  — module-level state
  update_trust(situation_type, outcome)          — asymmetric 20:1 delta
  get_trust_status(situation_type)               — single-situation getter
  get_all_trust_scores()                         — full state dump
  get_reward_summary()                           — RL reward aggregate (uses FEEDBACK_GIVEN)

The domain layer (services/feedback.py) owns SOC-specific seeding and resets.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal

from app.framework.feedback_store import FEEDBACK_GIVEN  # noqa: F401 — intra-framework

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state (owned here; services imports and extends)
# ---------------------------------------------------------------------------

# F6a: Asymmetric trust per situation type.
# Starting trust: 0.5.  Correct: +0.03 (cap 1.0).  Incorrect: −0.60 (floor 0.0).
TRUST_SCORES: Dict[str, float] = {}      # situation_type → current trust (0.0–1.0)
TRUST_HISTORY: List[Dict[str, Any]] = [] # one entry per trust update
LOW_TRUST_FLAGS: Dict[str, bool] = {}    # situation_type → human_review_required


# ---------------------------------------------------------------------------
# Trust functions
# ---------------------------------------------------------------------------

def update_trust(
    situation_type: str,
    outcome: Literal["correct", "incorrect"],
) -> Dict[str, Any]:
    """
    Update trust score for a situation type after a decision outcome.

    Asymmetric deltas (20:1 ratio):
        correct   → +0.03  (slow build-up of trust)
        incorrect → −0.60  (fast destruction of trust)

    Sets LOW_TRUST_FLAGS[situation_type] = True when trust drops below 0.3,
    which signals that human review should be required for this situation type.

    Returns
    -------
    The snapshot dict appended to TRUST_HISTORY.
    """
    if situation_type not in TRUST_SCORES:
        TRUST_SCORES[situation_type] = 0.5     # first encounter — start at neutral

    old_trust = TRUST_SCORES[situation_type]

    if outcome == "correct":
        delta = 0.03
        new_trust = min(old_trust + delta, 1.0)
    else:
        delta = -0.60
        new_trust = max(old_trust + delta, 0.0)

    new_trust = round(new_trust, 4)
    TRUST_SCORES[situation_type] = new_trust
    LOW_TRUST_FLAGS[situation_type] = new_trust < 0.3

    snap: Dict[str, Any] = {
        "decision_number": len(TRUST_HISTORY) + 1,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "situation_type":  situation_type,
        "trust_score":     new_trust,
        "delta":           round(delta, 4),
        "outcome":         outcome,
    }
    TRUST_HISTORY.append(snap)

    print(
        f"[TRUST] {situation_type}: {old_trust:.3f} → {new_trust:.3f} "
        f"(delta={delta:+.2f}, outcome={outcome})"
    )
    if LOW_TRUST_FLAGS[situation_type]:
        print(f"[TRUST] ⚠ {situation_type} trust={new_trust:.2f} < 0.30 — human review required")

    return snap


def get_trust_status(situation_type: str) -> Dict[str, Any]:
    """
    Get trust status for a single situation type.

    Returns
    -------
    {
      "situation_type":        str,
      "trust_score":           float (0.0–1.0),
      "human_review_required": bool   (True when trust < 0.3)
    }
    """
    trust_score = TRUST_SCORES.get(situation_type, 0.5)
    return {
        "situation_type":        situation_type,
        "trust_score":           round(trust_score, 4),
        "human_review_required": trust_score < 0.3,
    }


def get_all_trust_scores() -> Dict[str, Any]:
    """
    Return all current trust scores and the full update history.

    Returns
    -------
    {
      "trust_scores":         {situation_type: {trust_score, human_review_required}},
      "history":              [snapshots, oldest-first],
      "total_updates":        int,
      "low_trust_situations": [situation_types currently below 0.3]
    }
    """
    scores = {
        sit: {
            "trust_score":           round(score, 4),
            "human_review_required": score < 0.3,
        }
        for sit, score in TRUST_SCORES.items()
    }
    return {
        "trust_scores":         scores,
        "history":              list(TRUST_HISTORY),
        "total_updates":        len(TRUST_HISTORY),
        "low_trust_situations": [s for s, flag in LOW_TRUST_FLAGS.items() if flag],
    }


# ---------------------------------------------------------------------------
# Reward summary
# ---------------------------------------------------------------------------

def get_reward_summary() -> Dict[str, Any]:
    """
    Aggregate current in-memory feedback state into an RL reward summary.

    Reward signal:
        correct   → +0.3  (reinforces good decisions)
        incorrect → -6.0  (asymmetric penalty, ratio 20:1)

    Returns
    -------
    Dictionary with totals, cumulative reward, and loop governance info.
    """
    total_decisions = len(FEEDBACK_GIVEN)
    correct   = sum(1 for v in FEEDBACK_GIVEN.values() if v["outcome"] == "correct")
    incorrect = sum(1 for v in FEEDBACK_GIVEN.values() if v["outcome"] == "incorrect")
    cumulative_r_t = round(correct * 0.3 + incorrect * (-6.0), 4)

    return {
        "total_decisions":  total_decisions,
        "correct":          correct,
        "incorrect":        incorrect,
        "asymmetric_ratio": 20.0,
        "cumulative_r_t":   cumulative_r_t,
        "loop3_status":     "active" if total_decisions > 0 else "insufficient_data",
        "governs":          ["loop1_situation_analyzer", "loop2_agent_evolver"],
    }
