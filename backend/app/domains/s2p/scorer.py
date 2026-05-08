"""
S2P scorer - wires canonical S2PDomainConfig + GAE ProfileScorer.
Module-level singleton pattern (same as SOC gae_state.py).
"""

import numpy as np
from gae import ProfileScorer

from app.domains.s2p.config import S2PDomainConfig, LEARNING_ENABLED
from app.framework.iks_base import compute_iks as _compute_iks

# S2P D_MAX — same as SOC (PROD-1 validated)
S2P_D_MAX = 0.20

# Module-level scorer singleton
_scorer: ProfileScorer | None = None
_initialized: bool = False


def get_scorer() -> ProfileScorer:
    """Return the module-level S2P ProfileScorer. Auto-initializes."""
    global _scorer, _initialized
    if not _initialized:
        _scorer = _build_scorer()
        _initialized = True
    return _scorer


def reset_scorer() -> None:
    """Reset scorer to cold start. Used in tests."""
    global _scorer, _initialized
    _scorer = None
    _initialized = False


def _build_scorer() -> ProfileScorer:
    """Build a fresh S2P ProfileScorer from canonical S2PDomainConfig."""
    return ProfileScorer(
        mu=S2PDomainConfig.get_profile_centroids(),
        actions=S2PDomainConfig.actions,
        profile=S2PDomainConfig.get_calibration_profile(),
        categories=S2PDomainConfig.categories,
        eta_override=S2PDomainConfig.eta_override,
    )


def update_scorer(
    factor_vector: list[float],
    category: str,
    predicted_action: str,
    analyst_action: str,
) -> bool:
    """
    Update ProfileScorer centroids from analyst outcome.
    Only fires when LEARNING_ENABLED = True.
    Uses asymmetric η: confirm→eta_confirm=0.05, override→eta_override=0.01.
    Returns True if update applied, False if learning disabled.
    """
    if not LEARNING_ENABLED:
        return False

    scorer        = get_scorer()
    category_idx  = S2PDomainConfig.get_category_index(category)
    predicted_idx = S2PDomainConfig.get_action_index(predicted_action)
    analyst_idx   = S2PDomainConfig.get_action_index(analyst_action)
    correct       = (predicted_action == analyst_action)
    f             = np.array(factor_vector, dtype=float)

    scorer.update(
        f, category_idx, predicted_idx,
        correct=correct,
        gt_action_index=analyst_idx,
    )
    return True


def get_s2p_iks() -> dict:
    """
    Compute S2P IKS from current scorer state.

    Aligned with SOC/framework direction:
      IKS = drift score = 100 × min(mean_drift / D_MAX, 1.0)
    Grows as analyst decisions move centroids from the expert prior.
    Expert centroids are initialization, not accumulated institutional knowledge.
    """
    if not LEARNING_ENABLED:
        return {
            "iks": 0.0,
            "d_max": S2P_D_MAX,
            "mean_drift": 0.0,
            "decisions": 0,    # placeholder — Neo4j count added by endpoint
            "domain": "s2p",
            "status": "CALIBRATING",
            "learning_active": False,
            "interpretation": (
                "Cold start. S2P learning is disabled; verified outcomes have "
                "not been applied to institutional knowledge."
            ),
        }

    scorer = get_scorer()
    mu_zero = S2PDomainConfig.get_profile_centroids()

    # Framework returns {"current": drift_score, "mean_drift": ..., "estimated": bool}
    # where drift_score = 100 × min(mean_drift / d_max, 1.0)
    iks_result = _compute_iks(scorer.centroids, mu_zero, S2P_D_MAX)

    # Direct delegation — no inversion (aligned with SOC)
    iks_value = iks_result["current"]

    return {
        "iks":            round(iks_value, 1),
        "d_max":          S2P_D_MAX,
        "mean_drift":     iks_result["mean_drift"],
        "decisions":      0,    # placeholder — Neo4j count added by endpoint
        "domain":         "s2p",
        "status":         "ACTIVE",
        "learning_active": True,
        "interpretation": _interpret_iks(iks_value),
    }


def _interpret_iks(iks: float) -> str:
    if iks >= 80:
        return "High institutional knowledge. Scorer well-calibrated."
    elif iks >= 50:
        return "Moderate institutional knowledge. Calibration in progress."
    elif iks >= 20:
        return "Early learning. Centroids moving from prior."
    else:
        return "Cold start. Awaiting first analyst decisions."


def score_event(factor_vector: list[float], category: str) -> dict:
    """
    Score a procurement event.
    Returns: {action, action_index, confidence, probabilities}
    """
    scorer = get_scorer()
    category_index = S2PDomainConfig.get_category_index(category)
    f = np.array(factor_vector, dtype=float)
    result = scorer.score(f, category_index)
    return {
        "action":        result.action_name,
        "action_index":  result.action_index,
        "confidence":    float(result.confidence),
        "probabilities": [float(p) for p in result.probabilities],
    }
