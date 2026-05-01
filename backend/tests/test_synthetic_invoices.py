"""
tests/test_synthetic_invoices.py - S2P v2 synthetic invoice generator tests.
"""

import json
import os
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfigV2
from app.services.synthetic_invoices import SyntheticInvoiceGenerator


def _v2_factors():
    method = getattr(S2PDomainConfigV2, "get_factors", None)
    return list(method()) if callable(method) else list(S2PDomainConfigV2.factors)


def _v2_categories():
    method = getattr(S2PDomainConfigV2, "get_categories", None)
    return list(method()) if callable(method) else list(S2PDomainConfigV2.categories)


def _v2_actions():
    method = getattr(S2PDomainConfigV2, "get_actions", None)
    return list(method()) if callable(method) else list(S2PDomainConfigV2.actions)


def _fixture_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "app"
        / "data"
        / "s2p_demo_suppliers.json"
    )


def test_generate_returns_correct_count():
    generator = SyntheticInvoiceGenerator(seed=7)
    assert len(generator.generate(50)) == 50
    assert len(generator.generate(5000)) == 5000


def test_invoice_has_all_fields():
    invoice = SyntheticInvoiceGenerator(seed=7).generate(1)[0]
    for field_name in [
        "invoice_id",
        "supplier_id",
        "supplier_name",
        "category",
        "category_index",
        "ground_truth_action",
        "ground_truth_action_index",
        "factors",
        "factor_vector",
        "amount",
        "po_reference",
        "variance_pct",
    ]:
        assert hasattr(invoice, field_name)


def test_factor_vector_length_7():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(100)
    assert all(len(invoice.factor_vector) == 7 for invoice in invoices)


def test_factors_dict_has_7_keys():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(100)
    expected = _v2_factors()
    for invoice in invoices:
        assert len(invoice.factors) == 7
        assert list(invoice.factors.keys()) == expected


def test_factor_values_bounded_0_1():
    invoices = SyntheticInvoiceGenerator(seed=7, noise_level=0.30).generate(500)
    for invoice in invoices:
        assert all(0.0 <= value <= 1.0 for value in invoice.factor_vector)
        assert all(0.0 <= value <= 1.0 for value in invoice.factors.values())


def test_categories_are_v2():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(250)
    categories = set(_v2_categories())
    assert all(invoice.category in categories for invoice in invoices)


def test_actions_are_v2():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(250)
    actions = set(_v2_actions())
    assert all(invoice.ground_truth_action in actions for invoice in invoices)


def test_category_distribution_weighted():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(5000)
    counts = {category: 0 for category in _v2_categories()}
    for invoice in invoices:
        counts[invoice.category] += 1
    assert counts["price_variance"] > counts["duplicate_risk"]


def test_deterministic_with_seed():
    gen_a = SyntheticInvoiceGenerator(seed=123)
    gen_b = SyntheticInvoiceGenerator(seed=123)
    gen_c = SyntheticInvoiceGenerator(seed=124)

    exported_a = gen_a.export_as_scoring_input(gen_a.generate(100))
    exported_b = gen_b.export_as_scoring_input(gen_b.generate(100))
    exported_c = gen_c.export_as_scoring_input(gen_c.generate(100))

    assert exported_a == exported_b
    assert exported_a != exported_c


def test_noise_level_affects_spread():
    low_noise = SyntheticInvoiceGenerator(seed=9, noise_level=0.01).generate(5000)
    high_noise = SyntheticInvoiceGenerator(seed=9, noise_level=0.30).generate(5000)

    low_vectors = np.array([invoice.factor_vector for invoice in low_noise], dtype=float)
    high_vectors = np.array([invoice.factor_vector for invoice in high_noise], dtype=float)

    assert float(high_vectors.std(axis=0).mean()) > float(low_vectors.std(axis=0).mean()) * 1.15


def test_supplier_pool_has_10():
    assert len(SyntheticInvoiceGenerator(seed=7).suppliers) == 10


def test_supplier_names_realistic():
    names = [supplier.supplier_name for supplier in SyntheticInvoiceGenerator(seed=7).suppliers]
    assert any("Chen-Lin" in name for name in names)


def test_generate_supplier_fixture_serializable():
    fixture = SyntheticInvoiceGenerator(seed=7).generate_supplier_fixture()
    json.dumps(fixture)


def test_supplier_fixture_has_required_fields():
    fixture = SyntheticInvoiceGenerator(seed=7).generate_supplier_fixture()
    for supplier in fixture:
        assert {
            "supplier_id",
            "supplier_name",
            "region",
            "otif",
            "exception_rate",
            "lead_time",
            "financial_health_trend",
        }.issubset(supplier.keys())


def test_export_as_scoring_input_shape():
    generator = SyntheticInvoiceGenerator(seed=7)
    exported = generator.export_as_scoring_input(generator.generate(25))
    assert isinstance(exported, list)
    assert all(isinstance(row, dict) for row in exported)
    assert all(len(row["factor_vector"]) == 7 for row in exported)
    json.dumps(exported)


def test_invoice_ids_unique():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(5000)
    invoice_ids = [invoice.invoice_id for invoice in invoices]
    assert len(invoice_ids) == len(set(invoice_ids))


def test_po_references_plausible():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(500)
    assert all(re.match(r"^PO-\d{5}$", invoice.po_reference) for invoice in invoices)


def test_amounts_positive():
    invoices = SyntheticInvoiceGenerator(seed=7).generate(500)
    assert all(invoice.amount > 0 for invoice in invoices)


def test_scorer_accepts_generated_vectors():
    from gae import ProfileScorer

    generator = SyntheticInvoiceGenerator(seed=7)
    invoice = generator.generate(1)[0]
    scorer = ProfileScorer(
        mu=S2PDomainConfigV2.get_profile_centroids(),
        actions=_v2_actions(),
        profile=S2PDomainConfigV2.get_calibration_profile(),
        categories=_v2_categories(),
        eta_override=S2PDomainConfigV2.eta_override,
    )

    result = scorer.score(
        np.array(invoice.factor_vector, dtype=float),
        category_index=invoice.category_index,
    )
    assert 0 <= result.action_index < 5
    assert result.action_name in _v2_actions()


def test_fixture_file_exists_and_valid():
    fixture_path = _fixture_path()
    assert fixture_path.exists()
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert len(data) == 10


def test_oracle_quality_nearest_centroid_above_80_percent():
    generator = SyntheticInvoiceGenerator(seed=7, noise_level=0.08)
    invoices = generator.generate(5000)
    centroids = S2PDomainConfigV2.get_profile_centroids()

    correct = 0
    for invoice in invoices:
        vector = np.array(invoice.factor_vector, dtype=float)
        distances = np.linalg.norm(centroids[invoice.category_index] - vector, axis=1)
        nearest_action_index = int(np.argmin(distances))
        correct += int(nearest_action_index == invoice.ground_truth_action_index)

    assert correct / len(invoices) > 0.80


def test_fixture_chen_lin_matches_demo_script_values():
    data = json.loads(_fixture_path().read_text(encoding="utf-8"))
    chen_lin = next(supplier for supplier in data if supplier["supplier_name"] == "Chen-Lin Mfg")
    assert chen_lin["otif"]["q1_q2"] == 0.94
    assert chen_lin["otif"]["q3"] == 0.72
    assert chen_lin["exception_rate"]["baseline"] == 0.03
    assert chen_lin["exception_rate"]["current"] == 0.11
