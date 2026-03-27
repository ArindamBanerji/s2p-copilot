"""
ols_status.py — OLS (Override Lift Score) Dashboard service (L-09).

Uses GAE 0.7.18 OLSMonitor (CUSUM, plateau-snapshot baseline).
V-MV-CONSERVATION v2: OLS naturally declines during learning — this is
expected, not degradation. OLSMonitor excludes pre-plateau drift.

ACM (Analyst Competency Metric) activates only after analyst reaches
qualification_threshold overrides so that sparse-data noise is excluded.
"""

from gae import OLSMonitor
from typing import Optional


def get_ols_status(
    ols_history: list,
    warm_start_active: bool,
    analyst_overrides: dict,
    qualification_threshold: int = 20,
) -> dict:
    """
    Compute OLS dashboard status for the frontend.

    Parameters
    ----------
    ols_history          : list of float OLS values (one per decision)
    warm_start_active    : True → system is in warm-start; monitoring blocked
    analyst_overrides    : {analyst_id: override_count}
    qualification_threshold : minimum overrides before ACM activates

    Returns
    -------
    dict with keys:
        status           : "warming_up" | "monitoring" | "alarm"
        baseline_ols     : float or None (None before plateau)
        current_ols      : float or None (last value in history)
        delta_pct        : float or None (% change from baseline)
        cusum            : float (CUSUM statistic)
        alarm            : bool
        baseline_frozen  : bool
        qualified_analysts : int (analysts with >= qualification_threshold overrides)
        acm_active       : bool (True when ≥1 qualified analyst present)
        message          : str (human-readable summary)
    """
    # ── Warm-start block ────────────────────────────────────────────────────
    if warm_start_active or len(ols_history) < 2:
        return {
            "status": "warming_up",
            "baseline_ols": None,
            "current_ols": ols_history[-1] if ols_history else None,
            "delta_pct": None,
            "cusum": 0.0,
            "alarm": False,
            "baseline_frozen": False,
            "qualified_analysts": 0,
            "acm_active": False,
            "message": (
                "Warm-start active — OLS monitoring blocked until "
                "sufficient decisions accumulate."
                if warm_start_active
                else "Insufficient history — need ≥2 OLS observations."
            ),
        }

    # ── Run OLSMonitor ──────────────────────────────────────────────────────
    monitor = OLSMonitor()
    alarm_fired = False
    for ols_val in ols_history:
        fired = monitor.update(float(ols_val))
        if fired:
            alarm_fired = True

    current_ols = float(ols_history[-1])

    # ── delta_pct relative to frozen baseline ───────────────────────────────
    delta_pct: Optional[float] = None
    if monitor.baseline_frozen and monitor.baseline_ols > 0:
        delta_pct = round(
            100.0 * (current_ols - monitor.baseline_ols) / monitor.baseline_ols, 2
        )

    # ── ACM qualification ───────────────────────────────────────────────────
    qualified = sum(
        1 for count in analyst_overrides.values() if count >= qualification_threshold
    )
    acm_active = qualified >= 1

    # ── Status string ───────────────────────────────────────────────────────
    if alarm_fired or monitor.yellow_warning:
        status = "alarm"
        message = (
            f"OLS degradation detected. CUSUM={monitor.cusum:.3f}. "
            f"Current OLS={current_ols:.3f} vs baseline={monitor.baseline_ols:.3f}."
        )
    elif monitor.baseline_frozen:
        status = "monitoring"
        message = (
            f"OLS stable. Baseline frozen at {monitor.baseline_ols:.3f}. "
            f"Current={current_ols:.3f}."
        )
    else:
        status = "warming_up"
        message = (
            f"Plateau not yet reached ({len(ols_history)} observations). "
            f"Need {monitor.plateau_window} stable decisions."
        )

    return {
        "status": status,
        "baseline_ols": round(monitor.baseline_ols, 4) if monitor.baseline_frozen else None,
        "current_ols": round(current_ols, 4),
        "delta_pct": delta_pct,
        "cusum": round(monitor.cusum, 4),
        "alarm": alarm_fired or monitor.yellow_warning,
        "baseline_frozen": monitor.baseline_frozen,
        "qualified_analysts": qualified,
        "acm_active": acm_active,
        "message": message,
    }
