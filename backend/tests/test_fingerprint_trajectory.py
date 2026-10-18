import dataclasses
import json

import numpy as np
from fastapi.testclient import TestClient

from app.main import _json_safe, app


client = TestClient(app)


def test_fingerprint_returns_s2p_engine() -> None:
    response = client.get("/api/fingerprint")

    assert response.status_code == 200
    assert response.json()["engine"] == "s2p"


def test_trajectory_returns_s2p_engine() -> None:
    response = client.get("/api/trajectory")

    assert response.status_code == 200
    assert response.json()["engine"] == "s2p"


def test_fingerprint_response_is_non_empty_dict() -> None:
    data = client.get("/api/fingerprint").json()

    assert isinstance(data, dict)
    assert len(data) > 1
    assert any(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in data.values()
    )


def test_trajectory_response_is_non_empty_dict() -> None:
    data = client.get("/api/trajectory").json()

    assert isinstance(data, dict)
    assert len(data) > 1


def test_json_safe_converts_dataclass_to_dict() -> None:
    @dataclasses.dataclass
    class _TestDC:
        x: int = 1
        y: float = 2.5

    result = _json_safe(_TestDC())

    assert result == {"x": 1, "y": 2.5}
    assert isinstance(result, dict)


def test_json_safe_converts_numpy_types() -> None:
    assert isinstance(_json_safe(np.int64(42)), int)
    assert isinstance(_json_safe(np.float64(3.14)), float)
    assert _json_safe(np.float64(float("nan"))) is None
    assert _json_safe(np.float64(float("inf"))) is None


def test_json_safe_handles_nested_structures() -> None:
    nested = {
        "a": [np.int64(1), np.float64(2.0)],
        "b": {"c": np.bool_(True)},
    }

    result = _json_safe(nested)

    assert result == {"a": [1, 2.0], "b": {"c": True}}
    assert isinstance(result["b"]["c"], bool)


def test_responses_are_json_serializable() -> None:
    fingerprint_response = client.get("/api/fingerprint")
    trajectory_response = client.get("/api/trajectory")

    json.dumps(fingerprint_response.json())
    json.dumps(trajectory_response.json())
