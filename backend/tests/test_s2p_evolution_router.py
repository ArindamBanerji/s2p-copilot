import math

from fastapi.testclient import TestClient

from app.main import app
from app.services.s2p_evolver import get_evolution_summary, record_triage_outcome, reset_s2p_evolver


client = TestClient(app)


def assert_json_safe(value):
    if isinstance(value, dict):
        for item in value.values():
            assert_json_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_json_safe(item)
    elif isinstance(value, float):
        assert math.isfinite(value)
    elif value is None or isinstance(value, (str, int, bool)):
        return
    else:
        raise AssertionError(f"Unexpected non-JSON-safe type: {type(value)!r}")


def test_router_rules_returns_templates():
    response = client.get("/api/s2p/evolution/rules")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    assert len(data["rules"]) == 5
    assert {row["name"] for row in data["rules"]} >= {"auto_approve_threshold_sweep"}


def test_router_variants_returns_current_variants():
    reset_s2p_evolver()
    response = client.get("/api/s2p/evolution/variants")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    assert data["total"] >= 3
    assert any(row["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91" for row in data["variants"])
    assert data["sdk_summary"]["domain"] == "s2p"
    assert data["sdk_summary"]["variant_count"] == 4


def test_evolution_variants_endpoint():
    reset_s2p_evolver()

    response = client.get("/api/s2p/evolution/variants")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    sdk_summary = data["sdk_summary"]
    assert sdk_summary["families"] == ["evidence_ordering", "routing_threshold"]
    assert {row["id"] for row in sdk_summary["variants"]} >= {"EVIDENCE_ORDER_v1", "ROUTING_THRESHOLD_v1"}


def test_evolution_promotion_check_endpoint():
    reset_s2p_evolver()
    for _ in range(10):
        record_triage_outcome("EVIDENCE_ORDER_v2", is_correct=True, category="price_variance")

    response = client.get("/api/s2p/evolution/promotion-check")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    promotion = data["promotion"]
    assert promotion["promoted_id"] == "EVIDENCE_ORDER_v2"


def test_evolution_reset_endpoint():
    record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True, category="price_variance")

    response = client.post("/api/s2p/evolution/reset")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    assert data == {"status": "reset"}


def test_evolution_reset_reregisters_variants():
    record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True, category="price_variance")

    response = client.post("/api/s2p/evolution/reset")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    summary = get_evolution_summary()
    assert summary["variant_count"] == 4
    variant = next(row for row in summary["variants"] if row["id"] == "EVIDENCE_ORDER_v1")
    assert variant["status"] == "active"
    assert variant["total"] == 0


def test_router_shadow_results_returns_data():
    response = client.get(
        "/api/s2p/evolution/shadow-results",
        params={"variant_id": "auto_approve_threshold_sweep:price_variance:0.91"},
    )

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    assert data["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91"
    assert len(data["results"]) >= 3


def test_router_promoted_returns_promoted_rules():
    response = client.get("/api/s2p/evolution/promoted")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    promoted = data["promoted"]
    assert promoted["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91"
    assert promoted["state"] == "promoted"
