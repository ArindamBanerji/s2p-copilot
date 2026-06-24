import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app
from app.services.novelty_tracker import get_novelty_tracker, reset_novelty_tracker


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
    else:
        assert value is None or isinstance(value, (str, int, bool))


def assert_dict_response(path, params=None):
    response = client.get(path, params=params)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)
    return data


def test_novelty_status_returns_tracker_state():
    data = assert_dict_response("/api/s2p/novelty/status")

    assert "total_in_window" in data


def test_novelty_history_respects_limit_shape():
    data = assert_dict_response("/api/s2p/novelty/history", params={"limit": 5})

    assert isinstance(data["entries"], list)
    assert len(data["entries"]) <= 5


def test_novelty_rate_returns_category_rows():
    data = assert_dict_response("/api/s2p/novelty/rate")

    assert len(data["categories"]) == 5
    assert "overall_status" in data


def test_novelty_auto_pause_returns_advisory_payload():
    data = assert_dict_response("/api/s2p/novelty/auto-pause")

    assert isinstance(data["paused_categories"], list)
    assert data["advisory_only"] is True


def test_novelty_above_threshold_recommends_review():
    tracker = reset_novelty_tracker()
    for index in range(10):
        tracker.record([float(index)] * 7, "price_variance", 0.8 if index < 3 else 0.1)

    data = assert_dict_response("/api/s2p/novelty/status")

    assert data["conservation_review"] is True
    assert "Review" in data["recommendation"]


def test_single_hot_category_makes_top_level_amber():
    tracker = reset_novelty_tracker()
    for index in range(10):
        tracker.record([float(index)] * 7, "price_variance", 0.8 if index < 2 else 0.1)
        tracker.record([float(index)] * 7, "quantity_mismatch", 0.1)

    data = assert_dict_response("/api/s2p/novelty/status")

    assert data["status"] == "AMBER"
    assert data["conservation_review"] is True


def test_novelty_below_threshold_no_review():
    tracker = reset_novelty_tracker()
    for index in range(10):
        tracker.record([float(index)] * 7, "price_variance", 0.8 if index < 1 else 0.1)

    data = assert_dict_response("/api/s2p/novelty/status")

    assert data["conservation_review"] is False


def test_triggered_decisions_endpoint():
    tracker = reset_novelty_tracker()
    tracker.record([1.0] * 7, "price_variance", 0.9)

    data = assert_dict_response("/api/s2p/novelty/triggered-decisions")

    assert len(data["decisions"]) == 1
    assert data["decisions"][0]["is_novel"] is True


def test_novelty_status_includes_category():
    tracker = reset_novelty_tracker()
    tracker.record([1.0] * 7, "price_variance", 0.9)

    data = assert_dict_response("/api/s2p/novelty/rate")

    assert any(row["name"] == "price_variance" for row in data["categories"])


def test_novelty_history_sorted():
    tracker = reset_novelty_tracker()
    tracker.record([1.0] * 7, "price_variance", 0.9)
    tracker.record([2.0] * 7, "price_variance", 0.9)

    data = assert_dict_response("/api/s2p/novelty/triggered-decisions")

    assert [entry["sequence"] for entry in data["decisions"][:2]] == [2, 1]
