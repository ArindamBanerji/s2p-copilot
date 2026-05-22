from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.services.novelty_tracker import (
    NoveltyTracker,
    compute_nearest_distance,
    euclidean_distance,
    get_novelty_tracker,
    reset_novelty_tracker,
)


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
def reset_observability_state():
    reset_novelty_tracker()
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    yield
    reset_novelty_tracker()


def test_identical_vectors():
    vector = [0.1, 0.2, 0.3]
    assert euclidean_distance(vector, list(vector)) == 0.0


def test_known_distance():
    assert euclidean_distance([0.0, 0.0], [3.0, 4.0]) == 5.0


def test_seven_dim_distance():
    assert euclidean_distance([0.0] * 7, [1.0] * 7) == math.sqrt(7.0)


def test_mismatched_lengths_returns_inf():
    assert euclidean_distance([0.0], [0.0, 1.0]) == float("inf")


def test_empty_tracker_zero_novelty():
    tracker = NoveltyTracker()
    assert tracker.novelty_rate == 0.0
    assert tracker.alert_active is False


def test_all_normal_no_alert():
    tracker = NoveltyTracker(distance_threshold=0.6)
    for _ in range(10):
        tracker.record([0.1] * 7, "price_variance", 0.2)
    assert tracker.novelty_rate == 0.0
    assert tracker.alert_active is False


def test_high_novelty_triggers_alert():
    tracker = NoveltyTracker(distance_threshold=0.6)
    for _ in range(7):
        tracker.record([0.1] * 7, "price_variance", 0.2)
    for _ in range(3):
        tracker.record([0.9] * 7, "price_variance", 0.7)
    assert tracker.novelty_rate == 0.3
    assert tracker.alert_active is True


def test_exactly_20_percent_not_alert():
    tracker = NoveltyTracker(distance_threshold=0.6)
    for _ in range(8):
        tracker.record([0.1] * 7, "price_variance", 0.2)
    for _ in range(2):
        tracker.record([0.9] * 7, "price_variance", 0.7)
    assert tracker.novelty_rate == 0.2
    assert tracker.alert_active is False


def test_21_percent_triggers_alert():
    tracker = NoveltyTracker(window_size=100, distance_threshold=0.6)
    for _ in range(79):
        tracker.record([0.1] * 7, "price_variance", 0.2)
    for _ in range(21):
        tracker.record([0.9] * 7, "price_variance", 0.7)
    assert tracker.novelty_rate == 0.21
    assert tracker.alert_active is True


def test_window_slides():
    tracker = NoveltyTracker(window_size=3)
    for index in range(4):
        tracker.record([float(index)] * 7, "price_variance", 0.1)
    history = tracker.get_history()
    assert [entry["sequence"] for entry in history] == [2, 3, 4]
    assert tracker.get_status()["total_in_window"] == 3


def test_per_category_breakdown():
    tracker = NoveltyTracker(distance_threshold=0.6)
    tracker.record([0.1] * 7, "price_variance", 0.2)
    tracker.record([0.9] * 7, "price_variance", 0.7)
    tracker.record([0.9] * 7, "duplicate_risk", 0.7)
    breakdown = tracker.get_status()["per_category"]
    assert breakdown["price_variance"] == {"total": 2, "novel": 1, "novelty_rate": 0.5}
    assert breakdown["duplicate_risk"] == {"total": 1, "novel": 1, "novelty_rate": 1.0}


def test_status_shape():
    tracker = NoveltyTracker(window_size=12, distance_threshold=0.7)
    status = tracker.get_status()
    assert set(status) == {
        "window_size",
        "distance_threshold",
        "total_in_window",
        "novelty_count",
        "novelty_rate",
        "alert_active",
        "per_category",
    }
    assert status["window_size"] == 12
    assert status["distance_threshold"] == 0.7


def test_record_stores_vector_norm():
    tracker = NoveltyTracker()
    entry = tracker.record([3.0, 4.0], "price_variance", 0.1)
    assert entry["vector_norm"] == 5.0


def test_novelty_status_returns_200():
    response = client.get("/api/s2p/novelty/status")
    assert response.status_code == 200
    assert response.json()["total_in_window"] == 0


def test_novelty_history_returns_200():
    get_novelty_tracker().record([0.1] * 7, "price_variance", 0.2)
    response = client.get("/api/s2p/novelty/history")
    assert response.status_code == 200
    assert response.json()["total_in_window"] == 1
    assert len(response.json()["entries"]) == 1


def test_novelty_history_limit():
    tracker = get_novelty_tracker()
    for index in range(5):
        tracker.record([float(index)] * 7, "price_variance", 0.2)
    response = client.get("/api/s2p/novelty/history?limit=2")
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 2
    assert [entry["sequence"] for entry in entries] == [4, 5]


def test_novelty_routes_mounted():
    paths = {route.path for route in app.routes}
    assert "/api/s2p/novelty/status" in paths
    assert "/api/s2p/novelty/history" in paths


def test_nearest_distance_uses_scorer_centroids():
    scorer = app.state.scorer
    centroid = scorer.gae_scorer.centroids[0][0].tolist()
    distance = compute_nearest_distance(
        centroid,
        "price_variance",
        scorer,
        S2PDomainConfig,
    )
    assert distance == 0.0


def test_score_endpoint_records_novelty_if_centroids_available():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    data = response.json()
    assert "novelty" not in data
    status = get_novelty_tracker().get_status()
    assert status["total_in_window"] == 1
    entry = get_novelty_tracker().get_history()[0]
    assert entry["category"] == VALID_REQUEST["category"]
    assert len(entry["factor_vector"]) == S2PDomainConfig.n_factors
