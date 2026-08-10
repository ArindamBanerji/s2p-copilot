import math

from fastapi.testclient import TestClient

from app.main import app
from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG
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
    assert data["sdk_summary"]["variant_count"] == 8


def test_router_variants_filters_by_template_name():
    reset_s2p_evolver()
    response = client.get(
        "/api/s2p/evolution/variants",
        params={"template_name": "auto_approve_threshold_sweep"},
    )

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    assert data["total"] >= 1
    assert data["variants"]
    assert {row["template_name"] for row in data["variants"]} == {"auto_approve_threshold_sweep"}


def test_evolution_variants_endpoint():
    reset_s2p_evolver()

    response = client.get("/api/s2p/evolution/variants")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    sdk_summary = data["sdk_summary"]
    assert sdk_summary["families"] == [
        "evidence_ordering",
        "routing_threshold",
        "escalation_criteria",
        "triage_weights",
    ]
    variants = {row["id"]: row for row in sdk_summary["variants"]}
    assert set(variants) >= {
        "EVIDENCE_ORDER_v1",
        "ROUTING_THRESHOLD_v1",
        "ESCALATION_CRITERIA_v1",
        "TRIAGE_WEIGHTS_v1",
    }
    assert variants["ESCALATION_CRITERIA_v1"]["family"] == "escalation_criteria"
    assert variants["TRIAGE_WEIGHTS_v1"]["family"] == "triage_weights"


def test_evolution_promotion_check_endpoint():
    reset_s2p_evolver()
    for _ in range(S2P_EVOLVER_CONFIG.promotion_min_samples):
        record_triage_outcome("EVIDENCE_ORDER_v2", is_correct=True, category="price_variance")

    response = client.get("/api/s2p/evolution/promotion-check")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    promotion = data["promotion"]
    assert promotion["promoted"] is False
    assert promotion["reason"] == "conservation"


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
    assert summary["variant_count"] == 8
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


def test_router_shadow_results_without_variant_returns_all_results_mapping():
    response = client.get("/api/s2p/evolution/shadow-results")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    assert "total_variants" in data
    assert isinstance(data["results"], dict)


def test_router_promoted_returns_promoted_rules():
    response = client.get("/api/s2p/evolution/promoted")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    promoted = data["promoted"]
    assert promoted["variant_id"] == "auto_approve_threshold_sweep:price_variance:0.91"
    assert promoted["state"] == "promoted"
