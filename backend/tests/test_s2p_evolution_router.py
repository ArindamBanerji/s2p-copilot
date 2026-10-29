import math

from threading import Event

import pytest
from copilot_sdk.evolution.graph_store import GraphVariantStore
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from fastapi.testclient import TestClient

from app.main import app
from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG, S2P_VARIANTS
from app.services.s2p_evolver import (
    get_evolution_summary,
    get_evolver,
    record_triage_outcome,
    reset_s2p_evolver,
    set_graph_store,
)


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
    assert sdk_summary["families"] == list(dict.fromkeys(spec.family for spec in S2P_VARIANTS))
    variants = {row["id"]: row for row in sdk_summary["variants"]}
    assert set(variants) >= {
        "EVIDENCE_ORDER_v1",
        "ROUTING_THRESHOLD_v1",
        "ESCALATION_CRITERIA_v1",
        "TRIAGE_WEIGHTS_v1",
    }
    assert variants["ESCALATION_CRITERIA_v1"]["family"] == "escalation_criteria"
    assert variants["TRIAGE_WEIGHTS_v1"]["family"] == "triage_weights"


def test_evolution_families_follow_config_after_reverse_registration():
    graph_store = InMemoryGraphStore(domain="s2p")
    variant_store = GraphVariantStore(graph_store, "s2p")
    for spec in reversed(S2P_VARIANTS):
        variant_store.register_variant(spec)
    set_graph_store(graph_store)

    response = client.get("/api/s2p/evolution/variants")

    assert response.status_code == 200
    summary = response.json()["sdk_summary"]
    assert summary["variant_count"] == len(S2P_VARIANTS)
    assert summary["families"] == list(dict.fromkeys(spec.family for spec in S2P_VARIANTS))


def test_evolution_unknown_families_follow_present_config_families(monkeypatch: pytest.MonkeyPatch):
    evolver = get_evolver()
    # Keep one configured family (including its versions) to check deduplication
    # and ensure absent configured families are not invented by the summary.
    known_family = S2P_VARIANTS[-1].family
    raw_summary = evolver.get_summary()
    known_variants = [row for row in raw_summary["variants"] if row["family"] == known_family]
    # Supply out-of-order SDK summary rows independently of event replay/reset.
    raw_summary["variants"] = [
        {"family": "z_dynamic"}, *known_variants, {"family": "a_dynamic"},
    ]
    monkeypatch.setattr(evolver, "get_summary", lambda: raw_summary)

    assert get_evolution_summary()["families"] == [known_family, "a_dynamic", "z_dynamic"]
    raw_summary["variants"] = []
    assert get_evolution_summary()["families"] == []


def test_evolution_promotion_check_endpoint():
    reset_s2p_evolver()
    for _ in range(S2P_EVOLVER_CONFIG.promotion_min_samples):
        record_triage_outcome("EVIDENCE_ORDER_v2", is_correct=True, category="price_variance")

    response = client.get("/api/s2p/evolution/promotion-check")

    assert response.status_code == 200
    data = response.json()
    assert_json_safe(data)
    promotion = data["promotion"]
    # The live scorer provider is authoritative.  A healthy test fixture may
    # therefore promote the qualifying candidate; an unsafe scorer should
    # surface the live conservation gate that blocked promotion.
    if promotion.get("promoted") is False:
        assert promotion["reason"] == "conservation_gate_red"
    else:
        assert promotion["promoted_id"] == "EVIDENCE_ORDER_v2"


def test_promotion_check_returns_degraded_on_timeout(monkeypatch):
    """promotion-check returns degraded response when graph traversal exceeds timeout."""
    from app.routers import s2p_evolution

    monkeypatch.setattr(s2p_evolution, "PROMOTION_CHECK_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(s2p_evolution, "check_promotion", lambda *_args: Event().wait(1.0))

    response = client.get("/api/s2p/evolution/promotion-check")

    assert response.status_code == 200
    data = response.json()
    assert data.get("degraded") is True
    assert data["gate"] == "TIMEOUT"
    assert data["reason"] == "promotion_check_timeout"


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
