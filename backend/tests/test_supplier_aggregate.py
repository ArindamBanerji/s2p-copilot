from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app


client = TestClient(app)


def test_supplier_trends_contract_and_delta():
    response = client.get("/api/s2p/suppliers/trends")

    assert response.status_code == 200
    data = response.json()
    assert data["suppliers"]
    assert any(row["trend"] == "declining" for row in data["suppliers"])
    first = data["suppliers"][0]
    assert {"supplier_id", "name", "quarterly_otif", "trend", "trend_delta"}.issubset(first)
    values = [point["otif"] for point in first["quarterly_otif"]]
    assert first["trend_delta"] == round(values[-1] - values[0], 4)


def test_supplier_heatmap_contract_matrix_and_hotspots():
    response = client.get("/api/s2p/suppliers/heatmap")

    assert response.status_code == 200
    data = response.json()
    assert len(data["suppliers"]) == data["total"]
    assert data["categories"] == S2PDomainConfig.categories
    assert len(data["matrix"]) == data["total"]
    assert all(len(row) == S2PDomainConfig.n_categories for row in data["matrix"])
    assert all(item["rate"] > 0.15 for item in data["hot_spots"])


def test_supplier_correlations_contract_and_bounds():
    response = client.get("/api/s2p/suppliers/correlations")

    assert response.status_code == 200
    data = response.json()
    assert len(data["correlations"]) == data["supplier_count"]
    for row in data["correlations"]:
        assert {"supplier_id", "name", "exception_rate", "otif", "otif_exception_score", "risk_score"}.issubset(row)
        assert -1.0 <= row["otif_exception_score"] <= 1.0
        assert 0.0 <= row["risk_score"] <= 1.0
