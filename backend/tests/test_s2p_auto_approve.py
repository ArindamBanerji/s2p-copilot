from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.auto_approve import (
    AUTO_APPROVE_THRESHOLDS,
    SPOT_CHECK_RATE,
    record_auto_approve_decision,
    reset_auto_approve_stats,
    _should_auto_approve,
)
from app.main import app, build_s2p_scorer
from app.routers import s2p as s2p_router
from app.services.s2p_evolver import reset_s2p_evolver
from app.services.supplier_profile_accumulator import accumulator as supplier_profile_accumulator


client = TestClient(app)

VALID_REQUEST = {
    "event_id": "E001",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-001",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


@pytest.fixture(autouse=True)
def reset_state():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    reset_s2p_evolver()
    supplier_profile_accumulator.reset()
    reset_auto_approve_stats()
    yield
    reset_auto_approve_stats()


def test_auto_approve_above_threshold_green():
    result = _should_auto_approve(
        "price_variance",
        0.91,
        "GREEN",
        "auto_approve",
        spot_check_fn=lambda: False,
    )

    assert result["auto_approved"] is True
    assert result["reason"] == "approved"
    assert result["threshold"] == 0.90


def test_auto_approve_below_threshold():
    result = _should_auto_approve(
        "price_variance",
        0.89,
        "GREEN",
        "auto_approve",
        spot_check_fn=lambda: False,
    )

    assert result["auto_approved"] is False
    assert result["reason"] == "below_threshold"


def test_auto_approve_amber_conservation():
    result = _should_auto_approve(
        "price_variance",
        0.95,
        "AMBER",
        "auto_approve",
        spot_check_fn=lambda: False,
    )

    assert result["auto_approved"] is False
    assert result["reason"] == "conservation_not_green"


def test_auto_approve_wrong_action():
    result = _should_auto_approve(
        "price_variance",
        0.95,
        "GREEN",
        "hold_for_review",
        spot_check_fn=lambda: False,
    )

    assert result["auto_approved"] is False
    assert result["reason"] == "wrong_action"


def test_per_category_thresholds_different():
    assert AUTO_APPROVE_THRESHOLDS["duplicate_risk"] == 0.92
    assert AUTO_APPROVE_THRESHOLDS["format_compliance"] == 0.80
    assert AUTO_APPROVE_THRESHOLDS["duplicate_risk"] != AUTO_APPROVE_THRESHOLDS["format_compliance"]


def test_score_response_includes_auto_approve():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    auto_approve = response.json()["auto_approve"]
    assert auto_approve["category"] == "price_variance"
    assert auto_approve["threshold"] == AUTO_APPROVE_THRESHOLDS["price_variance"]
    assert auto_approve["action"] == response.json()["action"]
    assert auto_approve["confidence"] == response.json()["confidence"]
    assert "conservation_status" in auto_approve
    assert auto_approve["reason"] in {
        "approved",
        "below_threshold",
        "conservation_not_green",
        "wrong_action",
        "spot_check",
        "unknown_category",
    }


def test_spot_check_injectable():
    result = _should_auto_approve(
        "price_variance",
        0.95,
        "GREEN",
        "auto_approve",
        spot_check_fn=lambda: True,
    )

    assert result["spot_check"] is True
    assert result["auto_approved"] is False


def test_spot_check_false_injectable():
    result = _should_auto_approve(
        "price_variance",
        0.95,
        "GREEN",
        "auto_approve",
        spot_check_fn=lambda: False,
    )

    assert result["spot_check"] is False
    assert result["auto_approved"] is True


def test_spot_check_blocks_auto_approve():
    result = _should_auto_approve(
        "quantity_mismatch",
        0.90,
        "GREEN",
        "auto_approve",
        spot_check_fn=lambda: True,
    )

    assert result["reason"] == "spot_check"
    assert result["auto_approved"] is False


def test_spot_check_rate_constant():
    assert SPOT_CHECK_RATE == pytest.approx(0.02)


def test_expansion_proof_safe_to_expand(monkeypatch):
    monkeypatch.setattr(s2p_router, "_graph_verified_counts", lambda request: (25, 24))
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda request: "GREEN")

    response = client.get("/api/s2p/auto-approve/expansion-proof?category=price_variance")

    assert response.status_code == 200
    data = response.json()
    assert data["safe_to_expand"] is True
    assert data["proposed_threshold"] == pytest.approx(0.85)
    assert data["accuracy"] == pytest.approx(24 / 25)


def test_expansion_proof_insufficient_data(monkeypatch):
    monkeypatch.setattr(s2p_router, "_graph_verified_counts", lambda request: (5, 5))
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda request: "GREEN")

    response = client.get("/api/s2p/auto-approve/expansion-proof?category=price_variance")

    assert response.status_code == 200
    assert response.json()["safe_to_expand"] is False
    assert "20 are required" in response.json()["evidence"]


def test_expansion_proof_low_accuracy(monkeypatch):
    monkeypatch.setattr(s2p_router, "_graph_verified_counts", lambda request: (25, 20))
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda request: "GREEN")

    response = client.get("/api/s2p/auto-approve/expansion-proof?category=price_variance")

    assert response.status_code == 200
    assert response.json()["safe_to_expand"] is False
    assert response.json()["accuracy"] == pytest.approx(0.8)


def test_expansion_proof_includes_evidence(monkeypatch):
    monkeypatch.setattr(s2p_router, "_graph_verified_counts", lambda request: (25, 25))
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda request: "GREEN")

    response = client.get("/api/s2p/auto-approve/expansion-proof?category=duplicate_risk")

    assert response.status_code == 200
    data = response.json()
    assert data["evidence"]
    assert data["rollback_available"] is True


def test_expansion_proof_category_filter(monkeypatch):
    monkeypatch.setattr(s2p_router, "_graph_verified_counts", lambda request: (25, 25))
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda request: "GREEN")

    response = client.get("/api/s2p/auto-approve/expansion-proof?category=format_compliance")

    assert response.status_code == 200
    assert response.json()["category"] == "format_compliance"
    assert response.json()["current_threshold"] == pytest.approx(0.80)


def test_expansion_proof_unknown_category():
    response = client.get("/api/s2p/auto-approve/expansion-proof?category=unknown")

    assert response.status_code == 404


def test_auto_approve_stats_endpoint():
    record_auto_approve_decision(
        {
            "category": "price_variance",
            "auto_approved": True,
            "spot_check": False,
        }
    )

    response = client.get("/api/s2p/auto-approve/stats")

    assert response.status_code == 200
    data = response.json()
    assert data["total_auto_approved"] == 1
    assert data["current_auto_approve_rate"] == pytest.approx(1.0)
    assert data["source"] == "in_memory_demo_stats"


def test_auto_approve_stats_per_category():
    record_auto_approve_decision(
        {
            "category": "duplicate_risk",
            "auto_approved": False,
            "spot_check": True,
        }
    )

    response = client.get("/api/s2p/auto-approve/stats")

    data = response.json()
    assert data["per_category"]["duplicate_risk"]["held"] == 1
    assert data["per_category"]["duplicate_risk"]["threshold"] == pytest.approx(0.92)
    assert data["total_spot_checked"] == 1


def test_auto_approve_does_not_affect_conservation():
    before = client.get("/api/conservation/status").json()

    client.get("/api/s2p/auto-approve/stats")

    after = client.get("/api/conservation/status").json()
    assert after["verified_count"] == before["verified_count"]
    assert after["correct_count"] == before["correct_count"]
    assert after["status"] == before["status"]


def test_5_1_penalty_preserves_caution_or_threshold_behavior():
    result = _should_auto_approve(
        "contract_gap",
        0.879,
        "GREEN",
        "auto_approve",
        spot_check_fn=lambda: False,
    )

    assert result["auto_approved"] is False
    assert result["reason"] == "below_threshold"
    assert result["threshold"] == pytest.approx(0.88)
