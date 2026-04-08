"""
S2P Learning Activation Gate (Block 3.1)

Controls when S2P centroid updates activate from live procurement
decisions. Analogous to SOC Deployment Gate GREEN/AMBER/RED.

Trigger conditions (BOTH must hold):
  1. verified_decisions >= 50
  2. override_precision >= 0.40

Until gate opens: LEARNING_ENABLED = False (frozen mode).
After gate opens: LEARNING_ENABLED = True per-session.

Why these thresholds:
  - 50 decisions: enough to estimate centroid geometry reliably
  - 0.40 precision: minimum quality for safe centroid updates
    (below this, bad overrides corrupt the tensor)
"""

from dataclasses import dataclass
from typing import Optional

# Gate thresholds
MIN_VERIFIED_DECISIONS = 50
MIN_OVERRIDE_PRECISION = 0.40

# Per-factor sigma thresholds (S2P domain — lower noise than SOC)
# Procurement signals more stable → DiagonalKernel GREEN threshold
S2P_SIGMA_GREEN = 0.157   # same as SOC L2 GREEN
S2P_SIGMA_AMBER = 0.25    # same as SOC DiagonalKernel GREEN
S2P_SIGMA_RED   = 0.25    # above this → do not deploy learning


@dataclass
class S2PLearningGateResult:
    status: str              # "GREEN" | "AMBER" | "RED"
    learning_active: bool
    verified_decisions: int
    override_precision: float
    sigma_max: float
    reason: str
    recommendation: str
    gate_opened_at: Optional[int]  # epoch_ms when gate first opened, None if not yet


def evaluate_s2p_learning_gate(
    verified_decisions: int,
    override_precision: float,
    sigma_max: float = 0.0,
    gate_opened_at: Optional[int] = None,
) -> S2PLearningGateResult:
    """
    Evaluate whether S2P learning should be active.

    Args:
        verified_decisions: count of verified procurement decisions
        override_precision: fraction of overrides that were correct
        sigma_max: maximum per-factor sigma observed
        gate_opened_at: epoch_ms when gate first opened (None = not yet)

    Returns:
        S2PLearningGateResult with status and recommendation
    """
    # Check noise threshold first
    if sigma_max > S2P_SIGMA_RED and sigma_max > 0:
        return S2PLearningGateResult(
            status="RED",
            learning_active=False,
            verified_decisions=verified_decisions,
            override_precision=override_precision,
            sigma_max=sigma_max,
            reason=f"Factor noise too high (sigma_max={sigma_max:.3f} > {S2P_SIGMA_RED})",
            recommendation="Collect more decisions before enabling learning. "
                           "Review data quality.",
            gate_opened_at=None,
        )

    # Check decision count
    if verified_decisions < MIN_VERIFIED_DECISIONS:
        remaining = MIN_VERIFIED_DECISIONS - verified_decisions
        return S2PLearningGateResult(
            status="AMBER",
            learning_active=False,
            verified_decisions=verified_decisions,
            override_precision=override_precision,
            sigma_max=sigma_max,
            reason=f"Insufficient verified decisions "
                   f"({verified_decisions}/{MIN_VERIFIED_DECISIONS})",
            recommendation=f"Process {remaining} more verified procurement "
                           f"decisions to activate learning.",
            gate_opened_at=None,
        )

    # Check override precision
    if override_precision < MIN_OVERRIDE_PRECISION:
        return S2PLearningGateResult(
            status="AMBER",
            learning_active=False,
            verified_decisions=verified_decisions,
            override_precision=override_precision,
            sigma_max=sigma_max,
            reason=f"Override precision too low "
                   f"({override_precision:.1%} < {MIN_OVERRIDE_PRECISION:.0%})",
            recommendation="Review procurement team training. "
                           "Low precision overrides corrupt the risk model.",
            gate_opened_at=None,
        )

    # All conditions met — GREEN
    import time
    opened_at = gate_opened_at or int(time.time() * 1000)

    noise_status = "GREEN" if sigma_max <= S2P_SIGMA_GREEN else "AMBER"

    return S2PLearningGateResult(
        status=noise_status,
        learning_active=True,
        verified_decisions=verified_decisions,
        override_precision=override_precision,
        sigma_max=sigma_max,
        reason=f"All gate conditions met: "
               f"{verified_decisions} decisions, "
               f"{override_precision:.1%} precision",
        recommendation="Learning active. Monitor centroid drift via IKS.",
        gate_opened_at=opened_at,
    )
