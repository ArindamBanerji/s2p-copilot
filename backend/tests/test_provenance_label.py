from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.routers.s2p_data_helpers import (
    assert_no_sample_in_metric,
    is_sample_data,
    load_invoices,
)
from app.services.synthetic_invoices import SyntheticInvoiceGenerator


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
INVOICE_PATH = REPO_ROOT / "data" / "synthetic_invoices.json"
SUPPLIER_PATH = REPO_ROOT / "data" / "s2p_demo_suppliers.json"
APP_SUPPLIER_PATH = BACKEND_ROOT / "app" / "data" / "s2p_demo_suppliers.json"
EVOLUTION_PATH = BACKEND_ROOT / "data" / "s2p_evolution_fixture.json"


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_invoices_have_provenance():
    invoices = _read_json(INVOICE_PATH)

    assert len(invoices) == 50
    assert all(invoice.get("provenance") == "sample" for invoice in invoices)


def test_all_suppliers_have_provenance():
    suppliers = _read_json(SUPPLIER_PATH)

    assert len(suppliers) == 10
    assert all(supplier.get("provenance") == "sample" for supplier in suppliers)


def test_app_suppliers_in_sync():
    root_suppliers = _read_json(SUPPLIER_PATH)
    app_suppliers = _read_json(APP_SUPPLIER_PATH)

    assert len(app_suppliers) == len(root_suppliers) == 10
    assert all(supplier.get("provenance") == "sample" for supplier in app_suppliers)


def test_evolution_fixture_has_provenance():
    fixture = _read_json(EVOLUTION_PATH)

    assert fixture.get("provenance") == "sample"


def test_generator_emits_provenance():
    generator = SyntheticInvoiceGenerator(seed=7)
    invoices = generator.export_full_invoice_dicts(generator.generate(5))
    scoring_rows = generator.export_as_scoring_input(generator.generate(5))
    suppliers = generator.generate_supplier_fixture()

    assert all(invoice.get("provenance") == "sample" for invoice in invoices)
    assert all(row.get("provenance") == "sample" for row in scoring_rows)
    assert all(supplier.get("provenance") == "sample" for supplier in suppliers)


def test_load_invoices_preserves_provenance():
    invoices = load_invoices()

    assert invoices
    assert all(invoice.get("provenance") == "sample" for invoice in invoices)


def test_fixture_provenance_is_sample():
    fixture_paths = [
        INVOICE_PATH,
        SUPPLIER_PATH,
        APP_SUPPLIER_PATH,
    ]
    for path in fixture_paths:
        rows = _read_json(path)
        assert rows, f"{path.name} must not be empty"
        assert all(row.get("provenance") == "sample" for row in rows), path.name


def test_is_sample_data():
    assert is_sample_data({"provenance": "sample"}) is True
    assert is_sample_data({"provenance": "scraped_external"}) is False
    assert is_sample_data({}) is False


def test_assert_no_sample_raises():
    with pytest.raises(ValueError, match="F-26 VIOLATION"):
        assert_no_sample_in_metric([{"provenance": "sample"}], "supplier_scorecard")


def test_assert_no_sample_passes():
    assert_no_sample_in_metric([{"provenance": "scraped_external"}], "supplier_scorecard")
