"""S2P oracle pipeline experiments."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .buyer_oracle import BuyerOracle
from .holdout import ConditionalHoldout


def exp1_known_lift(n_per_arm: int = 5000) -> dict[str, float | bool]:
    """Known hold-rate lift: pipeline should recover the injected effect."""
    control = _sample_outcomes(BuyerOracle(seed=101), shown=False, n=n_per_arm)
    treatment = _sample_outcomes(BuyerOracle(seed=101), shown=True, n=n_per_arm)
    measured = _hold_rate(treatment) - _hold_rate(control)
    return {
        "pass": abs(measured - 0.08) <= 0.025,
        "measured": round(measured, 4),
        "expected": 0.08,
    }


def exp2_zero_lift(n_per_arm: int = 5000) -> dict[str, float | bool]:
    """Zero lift: pipeline should not report a material signal."""
    control = _sample_outcomes(BuyerOracle(treatment_lift=0.0, accuracy_lift=0.0, seed=202), shown=False, n=n_per_arm)
    treatment = _sample_outcomes(BuyerOracle(treatment_lift=0.0, accuracy_lift=0.0, seed=202), shown=True, n=n_per_arm)
    measured = _hold_rate(treatment) - _hold_rate(control)
    return {
        "pass": abs(measured) <= 0.025,
        "measured": round(measured, 4),
        "expected": 0.0,
    }


def exp3_floor_power(effect_size: float = 0.08, alpha_z: float = 1.96, power_z: float = 0.84) -> dict[str, int | bool]:
    """Compute a Gaussian lower-bound sample size for two hold-rate arms."""
    baseline = 0.40
    pooled_variance = baseline * (1.0 - baseline)
    n_per_arm = math.ceil(2.0 * ((alpha_z + power_z) ** 2) * pooled_variance / (effect_size**2))
    return {
        "pass": n_per_arm >= 500,
        "n_per_arm": n_per_arm,
    }


def exp4_gate_rejects(n_per_arm: int = 5000) -> dict[str, float | bool]:
    """Positive hold lift but negative accuracy: quality gate should reject."""
    control = _sample_outcomes(BuyerOracle(seed=303), shown=False, n=n_per_arm)
    treatment = _sample_outcomes(BuyerOracle(accuracy_lift=-0.08, seed=303), shown=True, n=n_per_arm)
    hold_lift = _hold_rate(treatment) - _hold_rate(control)
    accuracy_lift = _accuracy_rate(treatment) - _accuracy_rate(control)
    gate_rejected = hold_lift > 0.03 and accuracy_lift < 0.0
    return {
        "pass": gate_rejected,
        "gate_rejected": gate_rejected,
        "measured": round(hold_lift, 4),
        "accuracy_delta": round(accuracy_lift, 4),
    }


def exp5_conditional_coverage(total_suppliers: int = 10000) -> dict[str, float | bool]:
    """Conditional holdout coverage: 60% enriched * 15% holdout ~= 9% effective."""
    holdout = ConditionalHoldout(holdout_pct=15, seed=404)
    suppressed = 0
    enriched = 0
    for index in range(total_suppliers):
        has_enrichment = (index % 10) < 6
        enriched += int(has_enrichment)
        suppressed += int(
            holdout.suppressed(
                supplier_id=f"SUP-{index:05d}",
                has_enrichment=has_enrichment,
            )
        )

    effective_holdout_pct = suppressed / total_suppliers
    enriched_pct = enriched / total_suppliers
    return {
        "pass": abs(effective_holdout_pct - 0.09) <= 0.015,
        "effective_holdout_pct": round(effective_holdout_pct, 4),
        "enriched_pct": round(enriched_pct, 4),
        "expected": 0.09,
    }


def run_all_experiments() -> dict[str, dict]:
    """Run all S2P buyer-oracle pipeline experiments."""
    return {
        "exp1": exp1_known_lift(),
        "exp2": exp2_zero_lift(),
        "exp3": exp3_floor_power(),
        "exp4": exp4_gate_rejects(),
        "exp5": exp5_conditional_coverage(),
    }


def _sample_outcomes(oracle: BuyerOracle, *, shown: bool, n: int) -> list[dict]:
    return [oracle.synthetic_outcome(shown=shown) for _ in range(n)]


def _hold_rate(outcomes: Iterable[dict]) -> float:
    rows = list(outcomes)
    return sum(1 for row in rows if row["buyer_action"] == "hold_for_review") / len(rows)


def _accuracy_rate(outcomes: Iterable[dict]) -> float:
    rows = list(outcomes)
    return sum(1 for row in rows if row["correct"]) / len(rows)
