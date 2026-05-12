"""Graph-first S2P factor computer tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factor_protocol import FactorComputer
from app.domains.s2p import factors
from app.domains.s2p.factors import (
    ALL_FACTORS,
    FACTOR_NAMES,
    AmountVarianceRatio,
    CommodityIndexCorrelation,
    DuplicateScore,
    MatchStatus,
    PaymentTermsImpact,
    SupplierExceptionHistory,
    TaxRegulatoryCompliance,
    compute_all_factors,
)


BASE_INVOICE = {
    "invoice_id": "S2P-INV-0001",
    "supplier_id": "SUP-001",
    "amount": 1200.0,
    "factors": {
        "match_status": 0.42,
        "amount_variance_ratio": 0.33,
        "duplicate_score": 0.22,
        "supplier_exception_history": 0.11,
        "payment_terms_impact": 0.44,
        "commodity_index_correlation": 0.55,
        "tax_regulatory_compliance": 0.66,
    },
}


def test_registry_has_seven_canonical_factors():
    assert len(ALL_FACTORS) == 7
    assert FACTOR_NAMES == S2PDomainConfig.factors


def test_all_factors_implement_protocol():
    assert all(isinstance(factor, FactorComputer) for factor in ALL_FACTORS)
    assert all(hasattr(factor, "compute") for factor in ALL_FACTORS)


def test_match_status_graph_po_and_gr_low_risk():
    context = {
        "neighbors": [
            {"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}},
            {"node": {"_label": "GoodsReceipt", "gr_id": "GR-1"}},
        ]
    }
    assert MatchStatus().compute(BASE_INVOICE, context) == 0.1


def test_match_status_graph_po_without_gr_medium_risk():
    context = {"neighbors": [{"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}}]}
    assert MatchStatus().compute(BASE_INVOICE, context) == 0.6


def test_match_status_falls_back_to_invoice_factor():
    assert MatchStatus().compute(BASE_INVOICE) == 0.42


def test_amount_variance_ratio_uses_graph_purchase_order_amount():
    context = [{"node": {"_label": "PurchaseOrder", "po_id": "PO-1", "amount": 1000.0}}]
    assert AmountVarianceRatio().compute(BASE_INVOICE, context) == 0.2


def test_amount_variance_ratio_uses_variance_pct_before_factor_fallback():
    invoice = {**BASE_INVOICE, "variance_pct": 18}
    assert AmountVarianceRatio().compute(invoice) == 0.18


def test_duplicate_score_graph_skips_self_and_scores_sibling():
    context = {
        "neighbors": [
            {"node": {"_label": "Invoice", "invoice_id": "S2P-INV-0001", "amount": 1200.0}},
            {"node": {"_label": "Invoice", "invoice_id": "S2P-INV-0002", "amount": 1140.0}},
        ]
    }
    assert DuplicateScore().compute(BASE_INVOICE, context) == 0.95


def test_duplicate_score_graph_self_only_returns_zero():
    context = {"neighbors": [{"node": {"_label": "Invoice", "invoice_id": "S2P-INV-0001", "amount": 1200.0}}]}
    assert DuplicateScore().compute(BASE_INVOICE, context) == 0.0


def test_duplicate_score_falls_back_when_no_graph_context():
    assert DuplicateScore().compute(BASE_INVOICE) == 0.22


def test_supplier_exception_history_uses_graph_supplier_exception_rate():
    context = {"neighbors": [{"node": {"_label": "Supplier", "supplier_id": "SUP-001", "exception_rate": 0.27}}]}
    assert SupplierExceptionHistory().compute(BASE_INVOICE, context) == 0.27


def test_payment_terms_impact_parses_net_30_string():
    invoice = {**BASE_INVOICE, "payment_days": 45}
    context = {"neighbors": [{"node": {"_label": "Supplier", "supplier_id": "SUP-001", "payment_terms": "Net 30"}}]}
    assert PaymentTermsImpact().compute(invoice, context) == 0.5


def test_payment_terms_impact_missing_invoice_days_falls_back():
    context = {"neighbors": [{"node": {"_label": "Supplier", "supplier_id": "SUP-001", "payment_terms": "Net 30"}}]}
    assert PaymentTermsImpact().compute(BASE_INVOICE, context) == 0.44


def test_commodity_index_correlation_uses_graph_volatility():
    context = {"neighbors": [{"node": {"_label": "Commodity", "commodity_id": "CHEM", "volatility": 0.73}}]}
    assert CommodityIndexCorrelation().compute(BASE_INVOICE, context) == 0.73


def test_tax_regulatory_compliance_graph_contract_low_risk():
    context = {"neighbors": [{"node": {"_label": "Contract", "contract_id": "CTR-1"}}]}
    assert TaxRegulatoryCompliance().compute(BASE_INVOICE, context) == 0.15


def test_tax_regulatory_compliance_graph_without_contract_high_risk():
    context = {"neighbors": [{"node": {"_label": "Supplier", "supplier_id": "SUP-001"}}]}
    assert TaxRegulatoryCompliance().compute(BASE_INVOICE, context) == 0.8


def test_tax_regulatory_compliance_metadata_fallback():
    invoice = {
        **BASE_INVOICE,
        "metadata": {"tax_code": "TX-US", "withholding_tax": False},
    }
    assert TaxRegulatoryCompliance().compute(invoice) == 0.1


def test_compute_all_factors_returns_canonical_keys_and_clamped_values():
    invoice = {
        **BASE_INVOICE,
        "factors": {**BASE_INVOICE["factors"], "duplicate_score": 9.0},
    }
    values = compute_all_factors(invoice)
    assert list(values) == FACTOR_NAMES
    assert len(values) == 7
    assert all(0.0 <= value <= 1.0 for value in values.values())
    assert values["duplicate_score"] == 1.0


def test_compute_all_factors_uses_graph_context_for_selected_values():
    invoice = {**BASE_INVOICE, "payment_days": 60}
    context = {
        "neighbors": [
            {"node": {"_label": "PurchaseOrder", "po_id": "PO-1", "amount": 1000.0}},
            {"node": {"_label": "GoodsReceipt", "gr_id": "GR-1"}},
            {"node": {"_label": "Supplier", "supplier_id": "SUP-001", "exception_rate": 0.25, "payment_terms": "Net 30"}},
            {"node": {"_label": "Commodity", "commodity_id": "CHEM", "volatility": 0.8}},
            {"node": {"_label": "Contract", "contract_id": "CTR-1"}},
        ]
    }
    values = compute_all_factors(invoice, context)
    assert values["match_status"] == 0.1
    assert values["amount_variance_ratio"] == 0.2
    assert values["supplier_exception_history"] == 0.25
    assert values["payment_terms_impact"] == 1.0
    assert values["commodity_index_correlation"] == 0.8
    assert values["tax_regulatory_compliance"] == 0.15


def test_compute_all_factors_catches_factor_errors(monkeypatch):
    class BrokenFactor:
        name = "match_status"

        def compute(self, invoice, context=None):
            raise RuntimeError("boom")

    original = factors.ALL_FACTORS
    monkeypatch.setattr(factors, "ALL_FACTORS", [BrokenFactor(), *original[1:]])
    values = factors.compute_all_factors(BASE_INVOICE)
    assert values["match_status"] == 0.42
    assert len(values) == 7


def test_factor_module_has_no_sdk_or_soc_imports():
    source = factors.__file__
    text = open(source, encoding="utf-8").read()
    forbidden = ("from app.domains.soc", "from copilot_sdk", "import soc", "ci_platform")
    assert not any(token in text for token in forbidden)
