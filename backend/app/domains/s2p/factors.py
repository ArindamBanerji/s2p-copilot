"""
S2P Factor Computers.
Each factor takes invoice exception context and returns float in [0.0, 1.0].
Index order must match S2PDomainConfig.factors.
"""

from dataclasses import dataclass, field
from typing import Optional
import numpy as np

from app.domains.s2p.config import S2PDomainConfig


@dataclass
class S2PEvent:
    """
    Invoice exception context passed to all factor computers.

    Existing procurement fields are kept as request inputs, but the computed
    vector is always the canonical seven-factor S2P invoice vector.
    """
    event_id: str
    category: str
    amount: float
    supplier_id: str
    contract_id: Optional[str] = None
    approved_categories: list = field(default_factory=list)
    supplier_risk_rating: float = 0.5
    historical_spend_mean: float = 0.0
    historical_spend_std: float = 1.0
    days_since_last_audit: int = 90
    vendor_decisions: int = 0
    vendor_approvals: int = 0

    match_status: Optional[float] = None
    amount_variance_ratio: Optional[float] = None
    duplicate_score: Optional[float] = None
    supplier_exception_history: Optional[float] = None
    payment_terms_impact: Optional[float] = None
    commodity_index_correlation: Optional[float] = None
    tax_regulatory_compliance: Optional[float] = None


def _clamp(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


class MatchStatusFactor:
    name = "match_status"

    def compute(self, event: S2PEvent) -> float:
        if event.match_status is not None:
            return _clamp(event.match_status)
        if not event.approved_categories or not event.contract_id:
            return 0.5
        return 0.9 if event.category in event.approved_categories else 0.1


class AmountVarianceRatioFactor:
    name = "amount_variance_ratio"

    def compute(self, event: S2PEvent) -> float:
        if event.amount_variance_ratio is not None:
            return _clamp(event.amount_variance_ratio)
        if event.historical_spend_mean <= 0:
            return 0.3
        ratio = abs(event.amount - event.historical_spend_mean) / max(
            abs(event.historical_spend_mean),
            1.0,
        )
        return _clamp(ratio)


class DuplicateScoreFactor:
    name = "duplicate_score"

    def compute(self, event: S2PEvent) -> float:
        if event.duplicate_score is not None:
            return _clamp(event.duplicate_score)
        return 0.05


class SupplierExceptionHistoryFactor:
    name = "supplier_exception_history"

    def compute(self, event: S2PEvent) -> float:
        if event.supplier_exception_history is not None:
            return _clamp(event.supplier_exception_history)
        if event.vendor_decisions <= 0:
            return _clamp(1.0 - event.supplier_risk_rating)
        exception_rate = 1.0 - (event.vendor_approvals / event.vendor_decisions)
        return _clamp(exception_rate)


class PaymentTermsImpactFactor:
    name = "payment_terms_impact"

    def compute(self, event: S2PEvent) -> float:
        if event.payment_terms_impact is not None:
            return _clamp(event.payment_terms_impact)
        return 0.5


class CommodityIndexCorrelationFactor:
    name = "commodity_index_correlation"

    def compute(self, event: S2PEvent) -> float:
        if event.commodity_index_correlation is not None:
            return _clamp(event.commodity_index_correlation)
        return 0.5


class TaxRegulatoryComplianceFactor:
    name = "tax_regulatory_compliance"

    def compute(self, event: S2PEvent) -> float:
        if event.tax_regulatory_compliance is not None:
            return _clamp(event.tax_regulatory_compliance)
        return 0.9


S2P_FACTOR_COMPUTERS = [
    MatchStatusFactor(),
    AmountVarianceRatioFactor(),
    DuplicateScoreFactor(),
    SupplierExceptionHistoryFactor(),
    PaymentTermsImpactFactor(),
    CommodityIndexCorrelationFactor(),
    TaxRegulatoryComplianceFactor(),
]


def compute_factor_vector(event: S2PEvent) -> list[float]:
    """
    Compute all canonical S2P factors for an invoice event.
    Returns seven floats in S2PDomainConfig.factors order.
    """
    values = [fc.compute(event) for fc in S2P_FACTOR_COMPUTERS]
    if [fc.name for fc in S2P_FACTOR_COMPUTERS] != S2PDomainConfig.factors:
        raise RuntimeError("S2P factor computer order does not match config")
    return values
