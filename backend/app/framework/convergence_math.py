"""
Domain-agnostic convergence math for CopilotFramework.

CLAIM-CONV-01 (V-MV-CONVERGENCE v2, MAE=1.55d):
  predict_n_half()   — decisions to 50% convergence; V is NOT an input.
  decisions_to_days() — wall-clock conversion; V IS used here.

No domain-specific imports. Safe to copy to copilot-sdk.
"""

from typing import Literal

# ── CLAIM-CONV-01 regression coefficients ──────────────────────────────────
INTERCEPT = 28.5
COEFF_Q_BAR = -3.28
COEFF_SIGMA = -12.1            # higher sigma → slower (less signal)
KERNEL_DIAGONAL_OFFSET = -2.3  # diagonal converges faster than L2


def predict_n_half(
    sigma_mean: float,
    q_bar: float,
    kernel: Literal["l2", "diagonal"] = "l2",
) -> float:
    """
    Predict N_half (decisions to 50% convergence) from deployment params.
    CLAIM-CONV-01: MAE=1.55d validated. V is NOT an input.
    """
    kernel_offset = KERNEL_DIAGONAL_OFFSET if kernel == "diagonal" else 0.0
    n_half = (
        INTERCEPT
        + COEFF_Q_BAR * q_bar
        + COEFF_SIGMA * (1 - sigma_mean)
        + kernel_offset
    )
    return max(14.0, float(n_half))


def decisions_to_days(n_half_decisions: float, V: float, alpha: float = 0.25) -> float:
    """
    Convert decision count to calendar days.
    V IS used here — volume determines wall-clock time only, not calibration quality.
    """
    alerts_per_day_reaching_learning = max(V * alpha, 1.0)
    return round(n_half_decisions / alerts_per_day_reaching_learning, 1)
