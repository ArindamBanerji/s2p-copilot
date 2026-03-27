"""
S2P Factor Computers.
Each factor takes procurement event context and returns float in [0.0, 1.0].
0.0 = high risk signal. 1.0 = low risk / neutral.
Analogous to SOC factors but procurement domain.
"""

from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class S2PEvent:
    """
    Procurement event context passed to all factor computers.
    Analogous to SOC AlertContext.
    """
    event_id: str
    category: str                              # S2P_CATEGORIES member
    amount: float                              # spend amount in USD
    supplier_id: str
    contract_id: Optional[str] = None
    approved_categories: list = field(default_factory=list)  # categories in contract
    supplier_risk_rating: float = 0.5          # 0=high risk, 1=low risk
    historical_spend_mean: float = 0.0
    historical_spend_std: float = 1.0
    days_since_last_audit: int = 90
    vendor_decisions: int = 0                  # historical decisions for vendor
    vendor_approvals: int = 0                  # historical approvals for vendor


class SpendCategoryMatchFactor:
    """
    Factor: does spend category match approved contract categories?
    High match → 1.0 (low risk). No match → 0.0 (high risk).
    """
    name = "spend_category_match"

    def compute(self, event: S2PEvent) -> float:
        if not event.approved_categories or not event.contract_id:
            return 0.5  # no contract — neutral
        if event.category in event.approved_categories:
            return 0.9  # direct match — low risk
        return 0.1      # category mismatch — high risk


class SupplierRiskScoreFactor:
    """
    Factor: vendor risk rating from supplier risk register.
    Passes through supplier_risk_rating (0=high risk, 1=low risk).
    """
    name = "supplier_risk_score"

    def compute(self, event: S2PEvent) -> float:
        return float(np.clip(event.supplier_risk_rating, 0.0, 1.0))


class ContractComplianceFactor:
    """
    Factor: is spend within contract terms?
    Has contract + category match → high compliance.
    No contract → neutral.
    """
    name = "contract_compliance"

    def compute(self, event: S2PEvent) -> float:
        if not event.contract_id:
            return 0.5  # no contract — neutral
        if event.approved_categories and event.category in event.approved_categories:
            return 0.85  # in-contract spend
        return 0.15      # out-of-contract spend


class SpendAnomalyFactor:
    """
    Factor: is this spend amount anomalous vs historical baseline?
    Uses z-score against historical mean/std.
    High z-score → anomalous → low value (high risk).
    """
    name = "spend_anomaly"

    def compute(self, event: S2PEvent) -> float:
        if event.historical_spend_std == 0:
            return 0.5  # no history — neutral
        z = abs(event.amount - event.historical_spend_mean) / event.historical_spend_std
        # z=0 → 1.0 (normal). z≥3 → 0.1 (anomalous).
        return float(np.clip(1.0 - (z / 3.0) * 0.9, 0.1, 1.0))


class PatternHistoryFactor:
    """
    Factor: historical pattern score for this category (W2 analog).
    Accumulates over time as decisions are verified.
    Cold start: 0.5 (neutral). Warms up with W2 edges.
    """
    name = "pattern_history"

    def compute(self, event: S2PEvent) -> float:
        # Cold start — returns neutral until W2 graph accumulates
        # In production: query TRIGGERED_EVOLUTION edges for category
        return 0.5


class VendorTrustFactor:
    """
    Factor: accumulated vendor reliability from decision history.
    High approval rate → high trust → high value.
    Cold start: 0.5 (neutral).
    """
    name = "vendor_trust"

    def compute(self, event: S2PEvent) -> float:
        if event.vendor_decisions == 0:
            return 0.5  # cold start — neutral
        approval_rate = event.vendor_approvals / event.vendor_decisions
        return float(np.clip(approval_rate, 0.0, 1.0))


# Factor registry — ordered to match S2P_FACTORS index order
S2P_FACTOR_COMPUTERS = [
    SpendCategoryMatchFactor(),
    SupplierRiskScoreFactor(),
    ContractComplianceFactor(),
    SpendAnomalyFactor(),
    PatternHistoryFactor(),
    VendorTrustFactor(),
]


def compute_factor_vector(event: S2PEvent) -> list[float]:
    """
    Compute all 6 factors for a procurement event.
    Returns list of 6 floats in [0.0, 1.0].
    Index order matches S2P_FACTORS in config.py.
    """
    return [fc.compute(event) for fc in S2P_FACTOR_COMPUTERS]
