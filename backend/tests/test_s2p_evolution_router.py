from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_router_rules_returns_templates():
    response = client.get("/api/s2p/evolution/rules")

    assert response.status_code == 200
    data = response.json()
    assert len(data["rules"]) == 5
    assert {row["name"] for row in data["rules"]} >= {"auto_approve_threshold_sweep"}


def test_router_variants_returns_current_variants():
    response = client.get("/api/s2p/evolution/variants")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 3
    assert any(row["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91" for row in data["variants"])


def test_router_shadow_results_returns_data():
    response = client.get(
        "/api/s2p/evolution/shadow-results",
        params={"variant_id": "auto_approve_threshold_sweep:price_variance:0.91"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91"
    assert len(data["results"]) >= 3


def test_router_promoted_returns_promoted_rules():
    response = client.get("/api/s2p/evolution/promoted")

    assert response.status_code == 200
    data = response.json()["promoted"]
    assert data["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91"
    assert data["state"] == "promoted"
