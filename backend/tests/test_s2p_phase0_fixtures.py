"""Phase 0 committed S2P fixture tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np


BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from app.domains.s2p.config import S2PDomainConfig


DATA_DIR = REPO_ROOT / "data"
INVOICE_PATH = DATA_DIR / "synthetic_invoices.json"
SUPPLIER_PATH = DATA_DIR / "s2p_demo_suppliers.json"
CENTROID_PATH = DATA_DIR / "s2p_initial_centroids.json"

EXPECTED_CATEGORIES = [
    "price_variance",
    "quantity_mismatch",
    "duplicate_risk",
    "contract_gap",
    "format_compliance",
]
EXPECTED_ACTIONS = [
    "auto_approve",
    "hold_for_review",
    "escalate_to_buyer",
    "flag_leakage",
    "refer_to_specialist",
]
EXPECTED_FACTORS = [
    "match_status",
    "amount_variance_ratio",
    "duplicate_score",
    "supplier_exception_history",
    "payment_terms_impact",
    "commodity_index_correlation",
    "tax_regulatory_compliance",
]
ENRICHED_INVOICE_FIELDS = {
    "intent",
    "amount_at_risk",
    "amount_recovered",
    "cycle_time_hours",
    "verified",
}
ENRICHED_SUPPLIER_FIELDS = {
    "quarterly_otif",
    "behavioral_scores",
    "category_exception_rates",
    "monthly_volume",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_generator_module():
    generator_path = REPO_ROOT / "generators" / "s2p_synthetic.py"
    spec = importlib.util.spec_from_file_location("s2p_synthetic", generator_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _without_fields(rows: list[dict], fields: set[str]) -> list[dict]:
    return [
        {key: value for key, value in row.items() if key not in fields}
        for row in rows
    ]


def test_synthetic_invoices_count():
    invoices = _load_json(INVOICE_PATH)
    assert len(invoices) == 50


def test_synthetic_invoices_categories():
    invoices = _load_json(INVOICE_PATH)
    counts = Counter(invoice["category"] for invoice in invoices)
    assert set(counts) == set(EXPECTED_CATEGORIES)
    assert counts == {
        "price_variance": 15,
        "quantity_mismatch": 10,
        "duplicate_risk": 10,
        "contract_gap": 8,
        "format_compliance": 7,
    }


def test_synthetic_invoices_ground_truth():
    invoices = _load_json(INVOICE_PATH)
    action_counts = Counter((invoice["category"], invoice["ground_truth_action"]) for invoice in invoices)
    assert action_counts[("price_variance", "auto_approve")] == 10
    assert action_counts[("price_variance", "hold_for_review")] == 3
    assert action_counts[("price_variance", "flag_leakage")] == 2
    assert action_counts[("quantity_mismatch", "auto_approve")] == 5
    assert action_counts[("quantity_mismatch", "hold_for_review")] == 3
    assert action_counts[("quantity_mismatch", "escalate_to_buyer")] == 2
    assert action_counts[("duplicate_risk", "auto_approve")] == 2
    assert action_counts[("duplicate_risk", "hold_for_review")] == 3
    assert action_counts[("duplicate_risk", "flag_leakage")] == 5
    assert action_counts[("contract_gap", "auto_approve")] == 2
    assert action_counts[("contract_gap", "escalate_to_buyer")] == 4
    assert action_counts[("contract_gap", "refer_to_specialist")] == 2
    assert action_counts[("format_compliance", "auto_approve")] == 4
    assert action_counts[("format_compliance", "hold_for_review")] == 2
    assert action_counts[("format_compliance", "escalate_to_buyer")] == 1


def test_synthetic_invoice_factor_keys():
    invoices = _load_json(INVOICE_PATH)
    assert all(list(invoice["factors"]) == EXPECTED_FACTORS for invoice in invoices)


def test_synthetic_invoice_factor_values_range():
    invoices = _load_json(INVOICE_PATH)
    for invoice in invoices:
        assert invoice["currency"] == "USD"
        assert invoice["amount"] > 0
        assert invoice["category"] in EXPECTED_CATEGORIES
        assert invoice["ground_truth_action"] in EXPECTED_ACTIONS
        assert all(0.0 <= value <= 1.0 for value in invoice["factors"].values())
        assert {
            "invoice_date",
            "due_date",
            "line_items",
            "commodity",
            "contract_ref",
        }.issubset(invoice["metadata"])


def test_synthetic_suppliers_count():
    suppliers = _load_json(SUPPLIER_PATH)
    assert len(suppliers) == 10


def test_synthetic_supplier_fields():
    suppliers = _load_json(SUPPLIER_PATH)
    required = {
        "supplier_id",
        "name",
        "category",
        "exception_rate",
        "avg_invoice_amount",
        "payment_terms",
        "otif_score",
        "total_invoices",
        "total_exceptions",
        "recent_trend",
    }
    assert all(required.issubset(supplier) for supplier in suppliers)
    assert {
        "industrial chemicals",
        "logistics",
        "packaging",
        "IT services",
        "raw materials",
        "office services",
        "equipment maintenance",
        "utilities",
        "metals",
        "pharma/lab supplies",
    }.issubset({supplier["category"] for supplier in suppliers})


def test_initial_centroids_shape():
    centroids = _load_json(CENTROID_PATH)
    assert list(centroids) == EXPECTED_CATEGORIES
    for category in EXPECTED_CATEGORIES:
        assert list(centroids[category]) == EXPECTED_ACTIONS
        assert all(len(centroids[category][action]) == 7 for action in EXPECTED_ACTIONS)


def test_initial_centroids_values_in_range():
    centroids = _load_json(CENTROID_PATH)
    for category in EXPECTED_CATEGORIES:
        for action in EXPECTED_ACTIONS:
            assert all(0.0 <= value <= 1.0 for value in centroids[category][action])


def test_initial_centroids_anchor_cells():
    centroids = _load_json(CENTROID_PATH)
    assert centroids["price_variance"]["auto_approve"] == [0.95, 0.05, 0.02, 0.03, 0.50, 0.80, 0.95]
    assert centroids["price_variance"]["flag_leakage"] == [0.90, 0.40, 0.05, 0.25, 0.70, 0.15, 0.85]
    assert centroids["duplicate_risk"]["flag_leakage"] == [0.85, 0.10, 0.90, 0.40, 0.30, 0.50, 0.80]
    assert centroids["contract_gap"]["escalate_to_buyer"] == [0.70, 0.15, 0.05, 0.15, 0.85, 0.20, 0.60]


def test_generator_is_deterministic_against_committed_fixtures():
    module = _load_generator_module()
    generated_invoices = module.build_invoices()
    assert all(ENRICHED_INVOICE_FIELDS.issubset(invoice) for invoice in generated_invoices)
    assert module.SUPPLIERS == _without_fields(_load_json(SUPPLIER_PATH), ENRICHED_SUPPLIER_FIELDS)
    assert generated_invoices == _load_json(INVOICE_PATH)
    assert module.build_centroids() == _load_json(CENTROID_PATH)


def test_scorer_with_new_config():
    from gae import ProfileScorer

    scorer = ProfileScorer(
        mu=S2PDomainConfig.get_profile_centroids(),
        actions=S2PDomainConfig.get_actions(),
        profile=S2PDomainConfig.get_calibration_profile(),
        categories=S2PDomainConfig.get_categories(),
        eta_override=S2PDomainConfig.eta_override,
    )
    assert scorer.centroids.shape == (5, 5, 7)
    result = scorer.score(
        np.array([0.95, 0.05, 0.02, 0.03, 0.50, 0.80, 0.95], dtype=float),
        category_index=S2PDomainConfig.get_category_index("price_variance"),
    )
    assert result.action_name in EXPECTED_ACTIONS
