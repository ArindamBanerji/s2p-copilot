"""
IKS (Institutional Knowledge Score) algorithm for CopilotFramework.
compute_iks(mu, mu_zero, d_max) — domain-agnostic.
d_max is domain-calibrated (SOC: 0.20 from PROD-1 κ* validation).
Safe to copy to copilot-sdk.

Formula (docs/soc_copilot_design_v1.md §14):

    IKS(t) = 100 × min(
        mean( ‖μ(t)[c, a, :] − μ₀[c, a, :]‖₂  for all (c, a) )
        / d_max,
        1.0
    )

The caller is responsible for supplying mu_zero (loaded from the
domain-specific bootstrap sidecar) and d_max (domain-calibrated κ*).
When mu_zero is None, compute_iks returns an estimated score of 50.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _mean_centroid_drift(mu_t: np.ndarray, mu_zero: np.ndarray) -> float:
    """
    Compute mean ‖μ(t)[c,a,:] − μ₀[c,a,:]‖₂ over all (c, a) pairs.

    Parameters
    ----------
    mu_t    : np.ndarray, shape (n_categories, n_actions, n_factors)
    mu_zero : np.ndarray, same shape

    Returns
    -------
    float
        Mean L2 drift across all centroid slots.
    """
    diff = mu_t - mu_zero                    # shape (C, A, F)
    per_slot = np.linalg.norm(diff, axis=2)  # shape (C, A)
    return float(np.mean(per_slot))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_iks(
    mu_t: np.ndarray,
    mu_zero: Optional[np.ndarray],
    d_max: float,
) -> dict:
    """
    Compute the IKS score from centroid tensors.

    Parameters
    ----------
    mu_t    : np.ndarray, shape (n_categories, n_actions, n_factors)
              Current centroid tensor from ProfileScorer.
    mu_zero : np.ndarray or None
              Bootstrap prior.  When None, returns estimated IKS=50.
              The caller is responsible for loading the domain sidecar.
    d_max   : float
              Normalization constant (domain-calibrated κ*).
              SOC uses 0.20 (PROD-1, March 18).

    Returns
    -------
    dict with keys:
        current (float)      — IKS in [0, 100]
        mean_drift (float)   — raw mean L2 drift before normalization
        estimated (bool)     — True if mu_zero was unavailable
    """
    if mu_zero is None:
        log.debug("[IKS] mu_zero unavailable — returning estimated IKS=50")
        return {"current": 50.0, "mean_drift": 0.0, "estimated": True}

    if mu_zero.shape != mu_t.shape:
        log.warning(
            "[IKS] mu_zero shape %s ≠ mu(t) shape %s — IKS estimated (stale sidecar)",
            mu_zero.shape, mu_t.shape,
        )
        return {"current": 50.0, "mean_drift": 0.0, "estimated": True}

    mean_drift = _mean_centroid_drift(mu_t, mu_zero)
    iks = 100.0 * min(mean_drift / d_max, 1.0)
    return {"current": round(iks, 1), "mean_drift": round(mean_drift, 4), "estimated": False}


def interpret(iks_score: float) -> str:
    """Return a human-readable interpretation of the IKS (v1) score."""
    if iks_score < 20:
        return "Early learning \u2014 system is still close to priors"
    if iks_score < 50:
        return "Developing \u2014 meaningful drift from bootstrap detected"
    if iks_score < 80:
        return "Experienced \u2014 significant real-world adaptation"
    return "Mature \u2014 centroids substantially evolved from bootstrap"


def interpret_iks_v2(score: float) -> str:
    """Return a human-readable interpretation of the IKS v2 composite score."""
    if score < 10:
        return "Cold start — system is accumulating its first decisions"
    if score < 30:
        return "Early learning — building baseline across categories"
    if score < 60:
        return "Developing — meaningful institutional knowledge emerging"
    if score < 80:
        return "Mature — system has deep environment-specific knowledge"
    return "Expert — comprehensive institutional judgment established"
