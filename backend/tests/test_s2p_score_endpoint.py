"""
tests/test_s2p_score_endpoint.py — POST /api/s2p/score endpoint tests.

Run from backend/:
    pytest tests/test_s2p_score_endpoint.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

VALID_REQUEST = {
    "event_id": "E001",
    "category": "supplier_risk",
    "amount": 5000.0,
    "supplier_id": "SUP-001",
}


def test_score_endpoint_returns_200():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    assert response.status_code == 200


def test_score_response_has_required_fields():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    for key in ("event_id", "category", "action", "action_index",
                "confidence", "probabilities", "factor_vector", "factor_names"):
        assert key in data, f"Missing key: {key}"


def test_score_action_is_valid_s2p_action():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    action = response.json()["action"]
    assert action in ["approve", "escalate", "reject", "review"]


def test_score_factor_vector_length():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    assert len(data["factor_vector"]) == 6
    assert len(data["factor_names"]) == 6


def test_score_invalid_category_returns_422():
    bad_request = {**VALID_REQUEST, "category": "lateral_movement"}
    response = client.post("/api/s2p/score", json=bad_request)
    assert response.status_code == 422
