import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app

client = TestClient(app)
FIXTURE_SUPPLIERS = json.loads(Path("../data/s2p_demo_suppliers.json").read_text(encoding="utf-8"))


def test_list_suppliers_returns_non_empty_suppliers():
    response = client.get("/api/s2p/suppliers")

    assert response.status_code == 200
    data = response.json()
    assert data["suppliers"]
    assert data["total"] == len(data["suppliers"])


def test_supplier_count_matches_fixture():
    data = client.get("/api/s2p/suppliers").json()

    assert data["total"] == len(FIXTURE_SUPPLIERS)


def test_supplier_summaries_include_required_fields():
    supplier = client.get("/api/s2p/suppliers").json()["suppliers"][0]

    assert {
        "supplier_id",
        "name",
        "otif_score",
        "exception_rate",
        "invoice_count",
        "category_distribution",
        "trend_direction",
    }.issubset(supplier)


def test_exception_rate_between_zero_and_one():
    suppliers = client.get("/api/s2p/suppliers").json()["suppliers"]

    assert all(0.0 <= supplier["exception_rate"] <= 1.0 for supplier in suppliers)


def test_profile_returns_deterministic_trend_length_six():
    first = client.get("/api/s2p/suppliers/SUP-001/profile").json()
    second = client.get("/api/s2p/suppliers/SUP-001/profile").json()

    assert first["otif_trend"] == second["otif_trend"]
    assert first["exception_trend"] == second["exception_trend"]
    assert len(first["otif_trend"]) == 6
    assert len(first["exception_trend"]) == 6


def test_unknown_supplier_returns_404():
    response = client.get("/api/s2p/suppliers/UNKNOWN/profile")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_heatmap_returns_categories_with_rates():
    response = client.get("/api/s2p/suppliers/SUP-001/heatmap")

    assert response.status_code == 200
    data = response.json()
    assert data["supplier_id"] == "SUP-001"
    assert {item["category"] for item in data["categories"]} == set(S2PDomainConfig.categories)
    assert all(0.0 <= item["exception_rate"] <= 1.0 for item in data["categories"])


def test_heatmap_unknown_supplier_returns_404():
    response = client.get("/api/s2p/suppliers/UNKNOWN/heatmap")

    assert response.status_code == 404


def test_clustering_returns_known_cluster_names():
    data = client.get("/api/s2p/suppliers/clustering").json()
    names = {cluster["cluster_name"] for cluster in data["clusters"]}

    assert data["total_clusters"] == 4
    assert {"High Reliability", "Volume Leaders", "Risk Watch", "New/Low Volume"} == names


def test_clustered_supplier_ids_subset_of_supplier_list():
    supplier_ids = {supplier["supplier_id"] for supplier in client.get("/api/s2p/suppliers").json()["suppliers"]}
    clusters = client.get("/api/s2p/suppliers/clustering").json()["clusters"]
    clustered_ids = {supplier_id for cluster in clusters for supplier_id in cluster["supplier_ids"]}

    assert clustered_ids <= supplier_ids


def test_clustering_route_is_not_captured_as_supplier_id():
    response = client.get("/api/s2p/suppliers/clustering")

    assert response.status_code == 200
    assert "clusters" in response.json()


def test_profile_cross_references_invoice_fixture():
    profile = client.get("/api/s2p/suppliers/SUP-001/profile").json()

    assert profile["invoice_count"] >= len(profile["recent_invoices"])
    assert profile["top_categories"]


def test_no_soc_imports_or_vocabulary_in_suppliers_router():
    text = Path("app/routers/s2p_suppliers.py").read_text(encoding="utf-8").lower()

    for forbidden in (
        "from app.domains.soc",
        "import soc",
        "credential_access",
        "lateral_movement",
        "data_exfiltration",
        "escalate_soc",
        "suppress",
    ):
        assert forbidden not in text
