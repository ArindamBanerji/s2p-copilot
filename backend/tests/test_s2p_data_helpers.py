import ast
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.routers import s2p_data_helpers


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_load_invoices_returns_non_empty_list():
    invoices = s2p_data_helpers.load_invoices()

    assert invoices
    assert all(isinstance(invoice, dict) for invoice in invoices)


def test_load_invoices_records_have_invoice_id():
    invoice = s2p_data_helpers.load_invoices()[0]

    assert invoice.get("invoice_id") or invoice.get("event_id")


def test_load_suppliers_returns_non_empty_list():
    suppliers = s2p_data_helpers.load_suppliers()

    assert suppliers
    assert all(isinstance(supplier, dict) for supplier in suppliers)


def test_load_suppliers_records_have_supplier_id():
    supplier = s2p_data_helpers.load_suppliers()[0]

    assert supplier.get("supplier_id")


def test_missing_invoice_file_returns_empty_list(monkeypatch, tmp_path):
    monkeypatch.setattr(s2p_data_helpers, "_DATA_DIR", tmp_path)

    assert s2p_data_helpers.load_invoices() == []


def test_missing_supplier_file_returns_empty_list(monkeypatch, tmp_path):
    monkeypatch.setattr(s2p_data_helpers, "_DATA_DIR", tmp_path)

    assert s2p_data_helpers.load_suppliers() == []


def test_safe_routers_no_longer_define_duplicate_loaders():
    routers = [
        BACKEND_ROOT / "app" / "routers" / "s2p_control_tower.py",
        BACKEND_ROOT / "app" / "routers" / "s2p_evidence.py",
        BACKEND_ROOT / "app" / "routers" / "s2p_insight.py",
        BACKEND_ROOT / "app" / "routers" / "s2p_pvg.py",
        BACKEND_ROOT / "app" / "routers" / "s2p_suppliers.py",
    ]
    forbidden_names = {"_load_invoices", "_load_suppliers"}

    duplicates = []
    for router_path in routers:
        tree = ast.parse(router_path.read_text(encoding="utf-8"))
        duplicates.extend(
            f"{router_path}:{node.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name in forbidden_names
        )

    assert duplicates == []


def test_helper_contains_no_soc_vocabulary():
    source = (BACKEND_ROOT / "app" / "routers" / "s2p_data_helpers.py").read_text(encoding="utf-8").lower()

    for forbidden in ("credential_access", "lateral_movement", "malware_execution"):
        assert forbidden not in source
