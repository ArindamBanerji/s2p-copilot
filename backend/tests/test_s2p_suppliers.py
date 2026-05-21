import json
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
import pytest

from app.domains.s2p.config import S2PDomainConfig
from app.main import app
from app.services.supplier_profile_accumulator import accumulator

client = TestClient(app)
FIXTURE_SUPPLIERS = json.loads(Path("../data/s2p_demo_suppliers.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def reset_supplier_accumulator():
    accumulator.reset()
    yield
    accumulator.reset()


def _record_supplier_event(
    supplier_id: str = "SUP-TREND",
    invoice_date: str = "2024-01-01",
    correct: bool = True,
    invoice_id: str | None = None,
) -> None:
    invoice_id = invoice_id or f"INV-{supplier_id}-{invoice_date}"
    accumulator.on_decision_verified(
        {
            "category": "price_variance",
            "recommended_action": "auto_approve",
            "metadata": {
                "supplier_id": supplier_id,
                "supplier_name": f"{supplier_id} Supplier",
                "invoice_id": invoice_id,
                "invoice_date": invoice_date,
                "amount": 1000.0,
            },
        },
        {
            "actual_action": "auto_approve" if correct else "hold_for_review",
            "is_correct": correct,
            "reward": 1.0 if correct else -1.0,
        },
        {},
    )


def test_list_suppliers_returns_non_empty_suppliers():
    response = client.get("/api/s2p/suppliers")

    assert response.status_code == 200
    data = response.json()
    assert data["suppliers"]
    assert data["total"] == len(data["suppliers"])
    assert data["source"] == "accumulator_with_fixture_fallback"


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


def test_suppliers_list_returns_profiles():
    data = client.get("/api/s2p/suppliers").json()

    assert data["source"] == "accumulator_with_fixture_fallback"
    assert data["suppliers"][0]["source"] == "fixture"


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


def test_supplier_by_id_returns_profile():
    response = client.get("/api/s2p/suppliers/SUP-001/profile")

    assert response.status_code == 200
    profile = response.json()
    assert profile["supplier_id"] == "SUP-001"
    assert profile["source"] == "fixture"
    assert "risk_level" in profile


def test_supplier_history_returns_events():
    _record_supplier_event(supplier_id="SUP-HISTORY", invoice_date="2024-01-15")

    response = client.get("/api/s2p/suppliers/SUP-HISTORY/history")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["events"][0]["supplier_id"] == "SUP-HISTORY"


def test_supplier_history_unknown_returns_404():
    response = client.get("/api/s2p/suppliers/UNKNOWN/history")

    assert response.status_code == 404


def test_declining_returns_worsening_trend():
    for day in range(1, 11):
        _record_supplier_event(
            supplier_id="SUP-DECLINE",
            invoice_date=f"2024-01-{day:02d}",
            correct=day <= 5,
        )

    response = client.get("/api/s2p/suppliers/declining")

    assert response.status_code == 200
    data = response.json()
    assert any(row["supplier_id"] == "SUP-DECLINE" for row in data["suppliers"])


def test_declining_route_not_captured_as_supplier_id():
    response = client.get("/api/s2p/suppliers/declining")

    assert response.status_code == 200
    assert "suppliers" in response.json()


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
