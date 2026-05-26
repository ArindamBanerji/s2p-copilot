from __future__ import annotations

import json
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parents[2] / "data"
INVOICE_PATH = DATA_DIR / "synthetic_invoices.json"
SUPPLIER_PATH = DATA_DIR / "s2p_demo_suppliers.json"

INVOICE_BASE_FIELDS = {
    "amount",
    "category",
    "currency",
    "factors",
    "ground_truth_action",
    "invoice_id",
    "metadata",
    "po_number",
    "supplier_id",
    "supplier_name",
}
INVOICE_ENRICHED_FIELDS = {
    "intent",
    "amount_at_risk",
    "amount_recovered",
    "cycle_time_hours",
    "verified",
}
SUPPLIER_BASE_FIELDS = {
    "avg_invoice_amount",
    "category",
    "exception_rate",
    "name",
    "otif_score",
    "payment_terms",
    "recent_trend",
    "supplier_id",
    "total_exceptions",
    "total_invoices",
}
SUPPLIER_ENRICHED_FIELDS = {
    "quarterly_otif",
    "behavioral_scores",
    "category_exception_rates",
    "monthly_volume",
}
INTENT_BY_CATEGORY = {
    "price_variance": "invoice_price_variance",
    "quantity_mismatch": "invoice_match_failure",
    "duplicate_risk": "invoice_duplicate_risk",
    "contract_gap": "contract_compliance_gap",
    "format_compliance": "format_compliance_issue",
}
S2P_CATEGORIES = set(INTENT_BY_CATEGORY)
BEHAVIORAL_SCORE_KEYS = {
    "delivery_reliability",
    "pricing_stability",
    "exception_rate",
    "quality_score",
    "payment_responsiveness",
}


def _invoices() -> list[dict]:
    return json.loads(INVOICE_PATH.read_text(encoding="utf-8"))


def _suppliers() -> list[dict]:
    return json.loads(SUPPLIER_PATH.read_text(encoding="utf-8"))


def test_invoice_fixture_count_and_existing_fields_preserved():
    invoices = _invoices()

    assert len(invoices) == 50
    assert all(INVOICE_BASE_FIELDS.issubset(invoice) for invoice in invoices)


def test_invoice_enrichment_fields_and_intent_mapping():
    invoices = _invoices()

    assert all(INVOICE_ENRICHED_FIELDS.issubset(invoice) for invoice in invoices)
    for invoice in invoices:
        assert invoice["intent"] == INTENT_BY_CATEGORY[invoice["category"]]
        assert isinstance(invoice["verified"], bool)
        assert 0.5 <= float(invoice["cycle_time_hours"]) <= 48.0
        assert invoice["amount_at_risk"] == round(float(invoice["amount"]), 2)


def test_invoice_recovery_matches_verified_status():
    for invoice in _invoices():
        if invoice["verified"]:
            assert invoice["amount_recovered"] == round(float(invoice["amount"]) * 0.93, 2)
        else:
            assert invoice["amount_recovered"] is None


def test_supplier_fixture_count_and_existing_fields_preserved():
    suppliers = _suppliers()

    assert len(suppliers) == 10
    assert all(SUPPLIER_BASE_FIELDS.issubset(supplier) for supplier in suppliers)


def test_supplier_enrichment_fields_present_and_bounded():
    for supplier in _suppliers():
        assert SUPPLIER_ENRICHED_FIELDS.issubset(supplier)
        assert set(supplier["quarterly_otif"]) == {"Q3-2025", "Q4-2025", "Q1-2026", "Q2-2026"}
        assert set(supplier["behavioral_scores"]) == BEHAVIORAL_SCORE_KEYS
        assert set(supplier["category_exception_rates"]) == S2P_CATEGORIES
        assert len(supplier["monthly_volume"]) == 6
        assert all(isinstance(value, int) and value > 0 for value in supplier["monthly_volume"])

        values = list(supplier["behavioral_scores"].values())
        assert all(0.0 <= float(value) <= 1.0 for value in values)
        assert all(0.0 <= float(value) <= 1.0 for value in supplier["quarterly_otif"].values())


def test_supplier_trend_requirements():
    declining = 0
    improving = 0
    for supplier in _suppliers():
        values = list(supplier["quarterly_otif"].values())
        delta = values[-1] - values[0]
        if delta < -0.10:
            declining += 1
        if delta > 0.05:
            improving += 1

    assert declining >= 2
    assert improving >= 1


def test_supplier_category_exception_rates_sum_to_exception_rate():
    for supplier in _suppliers():
        total = sum(float(value) for value in supplier["category_exception_rates"].values())
        exception_rate = float(supplier["exception_rate"])

        assert abs(total - exception_rate) <= 0.02
