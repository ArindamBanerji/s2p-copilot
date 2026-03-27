"""
tests/test_s2p_iks.py — GET /api/s2p/iks endpoint tests.

Run from backend/:
    pytest tests/test_s2p_iks.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app
from app.domains.s2p.scorer import reset_scorer

client = TestClient(app)


def test_iks_endpoint_returns_200():
    response = client.get("/api/s2p/iks")
    assert response.status_code == 200


def test_iks_response_has_required_fields():
    response = client.get("/api/s2p/iks")
    data = response.json()
    assert "iks" in data
    assert "interpretation" in data
    assert "domain" in data
    assert data["domain"] == "s2p"


def test_iks_value_in_valid_range():
    response = client.get("/api/s2p/iks")
    assert 0.0 <= response.json()["iks"] <= 100.0


def test_iks_cold_start_value():
    reset_scorer()  # fresh scorer — no decisions, mu=0.5=mu_zero → drift=0
    response = client.get("/api/s2p/iks")
    assert response.json()["iks"] == 0.0
