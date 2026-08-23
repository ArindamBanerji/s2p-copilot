"""Contract tests for the five live S2P demo-beat endpoints."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_extinction_has_discover_shadow_promote_workflow() -> None:
    response = client.get("/api/s2p/evolution/extinction")
    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow"] == ["discover", "shadow", "promote"]
    assert isinstance(payload["extinct"], list)


def test_extinction_reports_promotion_and_trend_fields() -> None:
    payload = client.get("/api/s2p/evolution/extinction").json()
    assert isinstance(payload["promotion_count"], int)
    assert isinstance(payload["days_to_extinct_by_category"], dict)
    assert payload["extinction_rate_trend"]


def test_extinction_labels_evidence() -> None:
    payload = client.get("/api/s2p/evolution/extinction").json()
    assert payload["evidence_tier"] in {"T_S", "T_O"}
    assert payload["evidence_note"]


def test_frozen_twin_has_comparison_contract() -> None:
    response = client.get("/api/s2p/learning/frozen-twin")
    assert response.status_code == 200
    payload = response.json()
    for key in ("frozen_available", "frozen_decisions_would_miss", "delta_accuracy", "delta_coverage", "visual_diff"):
        assert key in payload


def test_frozen_twin_visual_diff_is_json_list() -> None:
    payload = client.get("/api/s2p/learning/frozen-twin").json()
    assert isinstance(payload["visual_diff"], list)
    assert payload["evidence_tier"] in {"T_A", "T_S"}


def test_frozen_twin_does_not_claim_accuracy_without_comparisons() -> None:
    payload = client.get("/api/s2p/learning/frozen-twin").json()
    if payload["compared_decisions"] == 0:
        assert payload["current_accuracy"] is None
        assert payload["frozen_accuracy"] is None


def test_what_if_unknown_invoice_is_not_fabricated() -> None:
    response = client.get("/api/s2p/context/what-if/unknown-invoice-for-test")
    assert response.status_code == 404


def test_what_if_route_has_expected_path() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/s2p/context/what-if/{invoice_id}" in paths


def test_what_if_unknown_invoice_explains_missing_evidence() -> None:
    payload = client.get("/api/s2p/context/what-if/unknown-invoice-for-test").json()
    assert "detail" in payload
    assert "not found" in payload["detail"].lower()


def test_day_zero_has_readiness_checklist() -> None:
    response = client.get("/api/s2p/diagnostics/day-zero")
    assert response.status_code == 200
    payload = response.json()
    for key in ("ready", "cannot_trust_yet", "coverage_per_category", "source_quality_gaps", "time_to_competence_estimate"):
        assert key in payload


def test_day_zero_covers_all_canonical_categories() -> None:
    payload = client.get("/api/s2p/diagnostics/day-zero").json()
    assert set(payload["coverage_per_category"]) == {
        "price_variance", "quantity_mismatch", "duplicate_risk", "contract_gap", "format_compliance"
    }


def test_day_zero_is_live_evidence_labelled() -> None:
    payload = client.get("/api/s2p/diagnostics/day-zero").json()
    assert payload["evidence_tier"] in {"T_O", "T_S"}
    assert "live" in payload["evidence_note"].lower()


def test_confidence_has_always_visible_contract() -> None:
    response = client.get("/api/s2p/diagnostics/confidence")
    assert response.status_code == 200
    payload = response.json()
    for key in ("per_decision", "categories_rising", "categories_falling", "novelty_observed", "evidence_tier"):
        assert key in payload


def test_confidence_rows_have_bands_and_novelty() -> None:
    payload = client.get("/api/s2p/diagnostics/confidence").json()
    for row in payload["per_decision"][:10]:
        assert row["band"] in {"high", "medium", "low"}
        assert "novel" in row


def test_confidence_evidence_note_is_present() -> None:
    payload = client.get("/api/s2p/diagnostics/confidence").json()
    assert payload["evidence_note"]


def test_rule_vs_reasoning_has_two_decision_paths() -> None:
    response = client.get("/api/s2p/context/rule-vs-reasoning")
    assert response.status_code == 200
    payload = response.json()
    assert {"rule_based", "situation_aware", "same_input", "contrast"} <= payload.keys()


def test_rule_vs_reasoning_exposes_computed_threshold() -> None:
    payload = client.get("/api/s2p/context/rule-vs-reasoning").json()
    rule = payload["rule_based"]
    assert rule["threshold_factor"] == "amount_variance_ratio"
    assert isinstance(rule["threshold"], float)
    assert isinstance(rule["value"], float)


def test_rule_vs_reasoning_keeps_evidence_labels_visible() -> None:
    payload = client.get("/api/s2p/context/rule-vs-reasoning").json()
    assert payload["rule_based"]["evidence_tier"] in {"T_S", "T_O"}
    assert payload["situation_aware"]["evidence_tier"] in {"T_S", "T_O"}
