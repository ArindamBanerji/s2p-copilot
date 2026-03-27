from typing import Dict


class FrozenROICalculator:
    """
    ROI for frozen scorer mode (LEARNING_ENABLED=False).

    Three value drivers, all from consistency alone:
    1. Time saved: analyst doesn't build context from scratch
    2. Consistency: same alert → same recommendation → no duplicated effort
    3. Coverage: 24/7 triage without 24/7 staffing

    Does NOT use $127/alert (that includes compounding value).
    Uses: 44 min baseline triage time × volume × analyst cost.

    Source: Three-judge consensus (Opus caught the $127 error).
    """

    def __init__(self,
                 analyst_hourly_cost: float = 85.0,
                 baseline_triage_minutes: float = 44.0,
                 alerts_per_day: float = 200.0,
                 working_days_per_year: int = 365,
                 auto_approve_rate: float = 0.04):
        self.analyst_cost = analyst_hourly_cost
        self.baseline_minutes = baseline_triage_minutes
        self.alerts_per_day = alerts_per_day
        self.working_days = working_days_per_year
        self.auto_approve_rate = auto_approve_rate

    def compute(self) -> Dict:
        """
        Compute frozen-mode annual ROI.

        Returns dict with:
          time_saved_per_alert_minutes: float
          annual_alerts: int
          auto_triaged_alerts: int
          manual_alerts: int
          hours_saved_annually: float
          cost_saved_annually: float
          consistency_value: float (duplicate effort eliminated)
          coverage_value: float (24/7 without 24/7 staffing)
          total_frozen_roi: float
          assumptions: Dict
        """
        annual_alerts = self.alerts_per_day * self.working_days
        auto_triaged = annual_alerts * self.auto_approve_rate

        # Time saved: auto-triaged alerts skip manual review entirely
        # Remaining alerts get faster review (context pre-built): ~15 min saved
        time_saved_auto = auto_triaged * self.baseline_minutes      # full time saved
        time_saved_assisted = (annual_alerts - auto_triaged) * 15   # 15 min/alert context
        total_minutes_saved = time_saved_auto + time_saved_assisted
        hours_saved = total_minutes_saved / 60
        cost_saved = hours_saved * self.analyst_cost

        # Consistency value: eliminates duplicate triage
        # Industry average: 8% of alerts triaged by >1 analyst
        duplicate_rate = 0.08
        consistency_value = annual_alerts * duplicate_rate * (self.baseline_minutes / 60) * self.analyst_cost

        # Coverage value: system triages during unstaffed hours
        # Assumes 8-hour gap in analyst coverage (nights/weekends)
        unstaffed_fraction = 8 / 24  # 1/3 of day
        coverage_alerts = annual_alerts * unstaffed_fraction * self.auto_approve_rate
        coverage_value = coverage_alerts * (self.baseline_minutes / 60) * self.analyst_cost

        total = cost_saved + consistency_value + coverage_value

        return {
            'time_saved_per_alert_minutes': 15.0,
            'annual_alerts': int(annual_alerts),
            'auto_triaged_alerts': int(auto_triaged),
            'manual_alerts': int(annual_alerts - auto_triaged),
            'hours_saved_annually': round(hours_saved, 1),
            'cost_saved_annually': round(cost_saved, 2),
            'consistency_value': round(consistency_value, 2),
            'coverage_value': round(coverage_value, 2),
            'total_frozen_roi': round(total, 2),
            'note': 'Frozen scorer ROI. Does not include learning lift.',
            'assumptions': {
                'analyst_hourly_cost': self.analyst_cost,
                'baseline_triage_minutes': self.baseline_minutes,
                'alerts_per_day': self.alerts_per_day,
                'auto_approve_rate': self.auto_approve_rate,
                'duplicate_triage_rate': duplicate_rate,
                'unstaffed_fraction': unstaffed_fraction
            }
        }
