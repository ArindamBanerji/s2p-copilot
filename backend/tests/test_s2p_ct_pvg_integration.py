import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
KNOWN_INVOICES = {
    invoice["invoice_id"]
    for invoice in json.loads(Path("../data/synthetic_invoices.json").read_text(encoding="utf-8"))
}


def test_all_new_routers_mounted_with_payload_invariants():
    checks = {
        "/api/s2p/control-tower/intents": "intents",
        "/api/s2p/control-tower/queue": "queue",
        "/api/s2p/pvg/variants": "variants",
        "/api/s2p/pvg/impact": "breakdown",
        "/api/s2p/pvg/leakage": "flagged_invoices",
        "/api/s2p/pvg/cycle-time": "activities",
        "/api/s2p/suppliers": "suppliers",
        "/api/s2p/suppliers/clustering": "clusters",
    }

    for path, key in checks.items():
        response = client.get(path)
        assert response.status_code == 200
        assert key in response.json()


def test_ct_queue_invoice_ids_correspond_to_known_invoices():
    queue = client.get("/api/s2p/control-tower/queue", params={"limit": 50}).json()["queue"]
    queue_ids = {item["invoice_id"] for item in queue}

    assert queue_ids
    assert queue_ids <= KNOWN_INVOICES


def test_pvg_leakage_ids_are_subset_of_known_invoices():
    leakage = client.get("/api/s2p/pvg/leakage").json()["flagged_invoices"]
    leakage_ids = {item["invoice_id"] for item in leakage}

    assert leakage_ids <= KNOWN_INVOICES


def test_supplier_clustering_ids_subset_of_supplier_list():
    supplier_ids = {supplier["supplier_id"] for supplier in client.get("/api/s2p/suppliers").json()["suppliers"]}
    clusters = client.get("/api/s2p/suppliers/clustering").json()["clusters"]
    clustered_ids = {supplier_id for cluster in clusters for supplier_id in cluster["supplier_ids"]}

    assert clustered_ids <= supplier_ids


def test_ct_priority_and_pvg_leakage_agree_on_high_variance_invoices():
    queue = client.get("/api/s2p/control-tower/queue", params={"limit": 50}).json()["queue"]
    leakage_ids = {
        item["invoice_id"]
        for item in client.get("/api/s2p/pvg/leakage").json()["flagged_invoices"]
    }
    high_variance_queue_ids = {
        item["invoice_id"]
        for item in queue
        if item["factors"]["amount_variance_ratio"] > 0.15
        and item["factors"]["commodity_index_correlation"] < 0.5
    }

    assert high_variance_queue_ids == leakage_ids


def test_no_soft_or_true_assertions_in_new_tests():
    forbidden = "or " + "True"
    for path in (
        Path("tests/test_s2p_control_tower.py"),
        Path("tests/test_s2p_pvg.py"),
        Path("tests/test_s2p_suppliers.py"),
        Path("tests/test_s2p_ct_pvg_integration.py"),
    ):
        assert forbidden not in path.read_text(encoding="utf-8")
