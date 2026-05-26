"""
tests/test_s2p_iks.py — GET /api/s2p/iks endpoint tests.

Run from backend/:
    pytest tests/test_s2p_iks.py -v
"""

import sys
import os
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app, build_s2p_scorer
from app.domains.s2p.config import S2PDomainConfig

client = TestClient(app)

SCORE_BODY = {
    "event_id": "IKS-001",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-IKS",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    return app.state.scorer


def assert_json_safe(value):
    if isinstance(value, dict):
        for item in value.values():
            assert_json_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_json_safe(item)
    elif isinstance(value, float):
        assert math.isfinite(value)
    else:
        assert value is None or isinstance(value, (str, int, bool))


def test_iks_endpoint_returns_200():
    response = client.get("/api/s2p/iks")
    assert response.status_code == 200


def test_iks_response_has_required_fields():
    reset_sdk_scorer()
    response = client.get("/api/s2p/iks")
    data = response.json()
    assert "iks" in data
    assert "d_max" in data
    assert "mean_drift" in data
    assert "decisions" in data
    assert "interpretation" in data
    assert "domain" in data
    assert "status" in data
    assert "learning_active" in data
    assert data["domain"] == "s2p"
    assert_json_safe(data)


def test_iks_value_in_valid_range():
    reset_sdk_scorer()
    response = client.get("/api/s2p/iks")
    assert 0.0 <= response.json()["iks"] <= 100.0


def test_legacy_s2p_scorer_module_removed():
    assert not Path("app/domains/s2p/scorer.py").exists()


def test_iks_endpoint_reports_cold_start_when_learning_disabled():
    reset_sdk_scorer()
    response = client.get("/api/s2p/iks")
    data = response.json()

    assert response.status_code == 200
    assert data["iks"] == 0.0
    assert data["status"] == "CALIBRATING"
    assert data["learning_active"] is False
    assert "High institutional knowledge" not in data["interpretation"]


def test_expert_centroids_are_priors_not_cold_start_knowledge():
    centroids = S2PDomainConfig.get_profile_centroids()
    assert centroids.shape == (5, 5, 7)
    assert not np.allclose(centroids, 0.5)


def test_iks_endpoint_uses_app_state_scorer(monkeypatch):
    class SentinelScorer:
        def trajectory(self):
            return SimpleNamespace(current_iks=42.5, decisions_total=7)

    monkeypatch.setattr(app.state, "scorer", SentinelScorer(), raising=False)

    response = client.get("/api/s2p/iks")
    data = response.json()

    assert response.status_code == 200
    assert data["iks"] == 42.5
    assert data["decisions"] == 7
    assert data["learning_active"] is True


def test_iks_endpoint_works_without_legacy_scorer_module():
    reset_sdk_scorer()
    response = client.get("/api/s2p/iks")
    assert response.status_code == 200


def test_score_learn_then_iks_reads_verified_sdk_state():
    reset_sdk_scorer()
    score_response = client.post(
        "/api/s2p/score",
        json={**SCORE_BODY, "event_id": "IKS-FLOW-001"},
    )
    assert score_response.status_code == 200
    score = score_response.json()

    learn_response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 80},
        },
    )
    assert learn_response.status_code == 200

    iks_response = client.get("/api/s2p/iks")
    data = iks_response.json()

    assert iks_response.status_code == 200
    assert data["decisions"] >= 1
    assert data["learning_active"] is True
    assert 0.0 <= data["iks"] <= 100.0
    assert_json_safe(data)
