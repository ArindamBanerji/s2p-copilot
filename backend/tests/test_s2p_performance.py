import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from gae.calibration import compute_theta_min

from app.main import app

client = TestClient(app)


class FakeGraphStore:
    def __init__(self):
        self.domain = "s2p"
        self.decisions = [
            {"decision_id": "D-1", "recommended_action": "auto_approve"},
            {"decision_id": "D-2", "recommended_action": "hold_for_review"},
            {"decision_id": "D-3", "action": "auto_approve"},
        ]
        self.verified = [
            {"decision_id": "D-1", "is_correct": True},
            {"decision_id": "D-2", "is_correct": False},
        ]

    def get_centroid_checkpoints(self, domain, **kwargs):
        assert domain == self.domain
        limit = kwargs.get("limit", 100)
        return [
            {"decision_id": "D-1", "category": "contract_gap", "centroids": {"auto_approve": [0.1]}},
            {"decision_id": "D-2", "category": "duplicate_risk", "centroids": {"hold_for_review": [0.2]}},
        ][:limit]

    def count_verified(self, domain):
        assert domain == self.domain
        return len(self.verified)

    def count_correct(self, domain):
        assert domain == self.domain
        return sum(1 for decision in self.verified if decision["is_correct"])

    def get_all_decisions(self, domain):
        assert domain == self.domain
        return list(self.decisions)

    def get_verified_decisions(self, domain):
        assert domain == self.domain
        return list(self.verified)


def with_fake_store():
    original = app.state.graph_store
    app.state.graph_store = FakeGraphStore()
    return original


def test_trajectory_returns_points():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/trajectory")
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    data = response.json()
    assert data["total_checkpoints"] == 2
    assert data["verified"] == 2
    assert data["current_q"] == 0.5


def test_trajectory_empty_safe():
    original = app.state.graph_store
    app.state.graph_store = object()
    try:
        response = client.get("/api/s2p/performance/trajectory")
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    assert response.json()["points"] == []


def test_what_if_returns_scenario():
    original = with_fake_store()
    try:
        response = client.get(
            "/api/s2p/performance/what-if",
            params={"additional_correct": 10, "additional_incorrect": 0},
        )
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    data = response.json()
    assert data["additional"]["correct"] == 10
    assert data["projected"]["verified"] == 12
    assert data["projected"]["q"] > data["current"]["q"]
    assert "theta_min" in data["projected"]


def test_what_if_uses_canonical_theta_min_formula():
    original = with_fake_store()
    try:
        response = client.get(
            "/api/s2p/performance/what-if",
            params={"additional_correct": 10, "additional_incorrect": 0},
        )
    finally:
        app.state.graph_store = original

    data = response.json()
    override_rate = 1 / 12
    expected = round(compute_theta_min(override_rate, 12), 4)
    assert data["projected"]["theta_min"] == expected


def test_performance_uses_compute_theta_min_not_penalty_denominator():
    source = Path("app/routers/s2p_performance.py").read_text(encoding="utf-8")
    assert "compute_theta_min" in source
    assert "PENALTY_RATIO * new_verified" not in source


def test_what_if_validates_input_bounds():
    response = client.get("/api/s2p/performance/what-if", params={"additional_correct": 101})

    assert response.status_code == 422


def test_summary_returns_metrics():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/summary")
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    data = response.json()
    assert data["total_scored"] == 3
    assert data["total_verified"] == 2
    assert data["accuracy"] == 0.5


def test_auto_approve_rate_in_range():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/summary")
    finally:
        app.state.graph_store = original

    assert 0.0 <= response.json()["auto_approve_rate"] <= 1.0


def test_savings_estimate_positive_when_decisions_seeded():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/summary")
    finally:
        app.state.graph_store = original

    assert response.json()["savings_estimate_usd"] > 0


def test_all_performance_endpoints_200():
    paths = [
        "/api/s2p/performance/trajectory",
        "/api/s2p/performance/what-if",
        "/api/s2p/performance/summary",
    ]

    for path in paths:
        assert client.get(path).status_code == 200
