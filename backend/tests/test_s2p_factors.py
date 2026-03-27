"""
tests/test_s2p_factors.py — S2P factor computer tests.

Run from backend/:
    pytest tests/test_s2p_factors.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.factors import (
    S2PEvent,
    SpendCategoryMatchFactor,
    SpendAnomalyFactor,
    VendorTrustFactor,
    S2P_FACTOR_COMPUTERS,
    compute_factor_vector,
)


def test_factor_vector_length():
    event = S2PEvent(event_id="E001", category="maverick_spend",
                     amount=5000.0, supplier_id="SUP-001")
    vector = compute_factor_vector(event)
    assert len(vector) == 6
    assert all(0.0 <= v <= 1.0 for v in vector)


def test_spend_category_match_direct_match():
    event = S2PEvent(event_id="E002", category="supplier_risk",
                     amount=1000.0, supplier_id="SUP-002",
                     approved_categories=["supplier_risk", "contract_breach"],
                     contract_id="C-001")
    factor = SpendCategoryMatchFactor()
    assert factor.compute(event) >= 0.8  # direct match → high value


def test_spend_category_match_mismatch():
    event = S2PEvent(event_id="E003", category="maverick_spend",
                     amount=1000.0, supplier_id="SUP-003",
                     approved_categories=["supplier_risk"],
                     contract_id="C-001")
    factor = SpendCategoryMatchFactor()
    assert factor.compute(event) <= 0.2  # mismatch → low value


def test_spend_anomaly_normal_spend():
    event = S2PEvent(event_id="E004", category="budget_overrun",
                     amount=1000.0, supplier_id="SUP-004",
                     historical_spend_mean=1000.0,
                     historical_spend_std=100.0)
    factor = SpendAnomalyFactor()
    assert factor.compute(event) >= 0.9  # z=0 → near 1.0


def test_spend_anomaly_extreme_outlier():
    event = S2PEvent(event_id="E005", category="budget_overrun",
                     amount=10000.0, supplier_id="SUP-005",
                     historical_spend_mean=1000.0,
                     historical_spend_std=100.0)
    factor = SpendAnomalyFactor()
    assert factor.compute(event) <= 0.2  # z=90 → near 0.1


def test_vendor_trust_cold_start():
    event = S2PEvent(event_id="E006", category="data_quality",
                     amount=500.0, supplier_id="SUP-006",
                     vendor_decisions=0, vendor_approvals=0)
    factor = VendorTrustFactor()
    assert factor.compute(event) == 0.5  # cold start → neutral


def test_factor_names_match_config():
    from app.domains.s2p.config import S2P_FACTORS
    names = [fc.name for fc in S2P_FACTOR_COMPUTERS]
    assert names == S2P_FACTORS  # order must match exactly
