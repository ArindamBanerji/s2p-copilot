"""
S2P scorer — wires S2PDomainConfig + GAE ProfileScorer.
Module-level singleton pattern (same as SOC gae_state.py).
Cold start: L2 kernel, uniform 0.5 centroids.
DiagonalKernel: after P28 Phase 2 measures per-factor sigma.
"""

import numpy as np
from gae import build_profile_scorer, KernelType, ProfileScorer
from gae import s2p_calibration_profile

from app.domains.s2p.config import S2PDomainConfig, LEARNING_ENABLED

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


def _build_scorer(kernel: KernelType = KernelType.L2) -> ProfileScorer:
    """
    Build a fresh S2P ProfileScorer from S2PDomainConfig.
    kernel: L2 (cold start) or DIAGONAL (after P28 qualification).
    """
    profile = s2p_calibration_profile()
    return build_profile_scorer(
        categories=S2PDomainConfig.categories,
        actions=S2PDomainConfig.actions,
        centroids=S2PDomainConfig.get_initial_centroids(),
        n_factors=S2PDomainConfig.n_factors,
        kernel=kernel,
        profile=profile,
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
