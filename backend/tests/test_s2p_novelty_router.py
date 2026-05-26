import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app


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
