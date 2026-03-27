"""
tests/test_s2p_outcome.py — POST /api/s2p/outcome endpoint tests.

Run from backend/:
    pytest tests/test_s2p_outcome.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

BASE = {
    "decision_id":      "S2P-E001-2026-01-01T00-00-00",
    "outcome":          "confirm",
    "analyst_action":   "approve",
    "analyst_id":       "A001",
    "factor_vector":    [0.9, 0.8, 0.85, 0.9, 0.5, 0.8],
    "category":         "supplier_risk",
    "predicted_action": "approve",
}


def test_outcome_endpoint_confirm_returns_200():
    response = client.post("/api/s2p/outcome", json=BASE)
    assert response.status_code == 200
    assert response.json()["outcome"] == "confirm"


def test_outcome_endpoint_override_returns_200():
    payload = {**BASE, "outcome": "override", "analyst_action": "escalate",
               "predicted_action": "approve"}
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 200


def test_learning_disabled_by_default():
    response = client.post("/api/s2p/outcome", json=BASE)
    assert response.json()["learning_applied"] == False


def test_invalid_outcome_returns_422():
    payload = {**BASE, "outcome": "approve"}   # not "confirm" or "override"
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 422


def test_invalid_analyst_action_returns_422():
    payload = {**BASE, "analyst_action": "suppress"}   # SOC action
    response = client.post("/api/s2p/outcome", json=payload)
    assert response.status_code == 422
