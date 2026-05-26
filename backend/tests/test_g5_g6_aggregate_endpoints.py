from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.main import app


client = TestClient(app)


def test_financial_impact_returns_computed_fixture_totals():
    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    data = response.json()
    assert {
        "total_recovered",
        "total_at_risk",
        "total_leakage_prevented",
        "by_category",
        "auto_approve_savings_hours",
        "source",
    }.issubset(data)
    assert data["source"] == "fixture"
    assert data["total_at_risk"] > 0
    assert data["total_recovered"] > 0
    assert set(data["by_category"]) == set(S2PDomainConfig.categories)
    assert all(
        {"recovered", "at_risk", "count"}.issubset(payload)
        for payload in data["by_category"].values()
    )


def test_existing_pvg_paths_still_return_200_after_router_prefix_change():
    for path in (
        "/api/s2p/pvg/variants",
        "/api/s2p/pvg/impact",
        "/api/s2p/pvg/leakage",
        "/api/s2p/pvg/cycle-time",
    ):
        assert client.get(path).status_code == 200


def test_supplier_trends_aggregate_returns_declining_and_improving_counts():
    response = client.get("/api/s2p/suppliers/trends")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "fixture"
    assert data["total"] == 10
    assert data["declining_count"] >= 2
    assert data["improving_count"] >= 1
    assert data["suppliers"]
    assert {"supplier_id", "name", "quarterly_otif", "trend", "trend_delta"}.issubset(data["suppliers"][0])
    assert data["trends"]
    assert {"supplier_id", "quarterly_otif", "otif_delta", "direction", "signals"}.issubset(data["trends"][0])


def test_supplier_aggregate_heatmap_returns_all_categories_without_shadowing_detail_route():
    aggregate = client.get("/api/s2p/suppliers/heatmap")
    detail = client.get("/api/s2p/suppliers/SUP-001/heatmap")

    assert aggregate.status_code == 200
    assert detail.status_code == 200
    data = aggregate.json()
    assert data["source"] == "fixture"
    assert data["total"] == 10
    assert len(data["suppliers"]) == 10
    assert len(data["matrix"]) == 10
    assert all(len(row) == len(S2PDomainConfig.categories) for row in data["matrix"])
    assert set(data["categories"]) == set(S2PDomainConfig.categories)
    assert all(item["rate"] > 0.15 for item in data["hot_spots"])
    assert set(data["category_totals"]) == set(S2PDomainConfig.categories)
    assert set(data["supplier_details"][0]["category_exception_rates"]) == set(S2PDomainConfig.categories)


def test_supplier_correlations_return_bounded_coefficients():
    response = client.get("/api/s2p/suppliers/correlations")

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "fixture"
    assert data["supplier_count"] == 10
    assert data["correlations"]
    for correlation in data["correlations"]:
        assert {"supplier_id", "name", "exception_rate", "otif", "otif_exception_score", "risk_score"}.issubset(correlation)
        assert -1.0 <= correlation["otif_exception_score"] <= 1.0
        assert 0.0 <= correlation["risk_score"] <= 1.0
    for correlation in data["metric_correlations"]:
        assert {"metric_x", "metric_y", "coefficient"}.issubset(correlation)
        assert -1.0 <= correlation["coefficient"] <= 1.0


def test_existing_supplier_paths_still_return_200():
    for path in (
        "/api/s2p/suppliers",
        "/api/s2p/suppliers/clusters",
        "/api/s2p/suppliers/clustering",
        "/api/s2p/suppliers/declining",
        "/api/s2p/suppliers/early-warnings",
        "/api/s2p/suppliers/trend-signals?supplier_id=SUP-001",
    ):
        assert client.get(path).status_code == 200
