"""
tests/test_s2p_factors.py - canonical S2P factor computer tests.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.factors import (
    S2PEvent,
    MatchStatusFactor,
    AmountVarianceRatioFactor,
    SupplierExceptionHistoryFactor,
    TaxRegulatoryComplianceFactor,
    S2P_FACTOR_COMPUTERS,
    compute_factor_vector,
)


def test_factor_vector_length():
    event = S2PEvent(event_id="E001", category="price_variance",
                     amount=5000.0, supplier_id="SUP-001")
    vector = compute_factor_vector(event)
    assert len(vector) == 7
    assert all(0.0 <= v <= 1.0 for v in vector)


def test_match_status_direct_match():
    event = S2PEvent(event_id="E002", category="price_variance",
                     amount=1000.0, supplier_id="SUP-002",
                     approved_categories=["price_variance", "contract_gap"],
                     contract_id="C-001")
    assert MatchStatusFactor().compute(event) >= 0.8


def test_match_status_mismatch():
    event = S2PEvent(event_id="E003", category="price_variance",
                     amount=1000.0, supplier_id="SUP-003",
                     approved_categories=["contract_gap"],
                     contract_id="C-001")
    assert MatchStatusFactor().compute(event) <= 0.2


def test_amount_variance_ratio_uses_existing_amount_fields():
    event = S2PEvent(event_id="E004", category="price_variance",
                     amount=1200.0, supplier_id="SUP-004",
                     historical_spend_mean=1000.0)
    assert AmountVarianceRatioFactor().compute(event) == 0.2


def test_supplier_exception_history_from_vendor_history():
    event = S2PEvent(event_id="E005", category="quantity_mismatch",
                     amount=500.0, supplier_id="SUP-005",
                     vendor_decisions=100, vendor_approvals=82)
    assert abs(SupplierExceptionHistoryFactor().compute(event) - 0.18) < 1e-9


def test_explicit_canonical_factor_overrides_are_clamped():
    event = S2PEvent(event_id="E006", category="format_compliance",
                     amount=500.0, supplier_id="SUP-006",
                     tax_regulatory_compliance=1.5)
    assert TaxRegulatoryComplianceFactor().compute(event) == 1.0


def test_factor_names_match_config():
    from app.domains.s2p.config import S2P_FACTORS

    names = [fc.name for fc in S2P_FACTOR_COMPUTERS]
    assert names == S2P_FACTORS
