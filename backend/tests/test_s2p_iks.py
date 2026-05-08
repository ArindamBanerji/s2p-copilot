"""
tests/test_s2p_iks.py — GET /api/s2p/iks endpoint tests.

Run from backend/:
    pytest tests/test_s2p_iks.py -v
"""

import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app
from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.scorer import get_s2p_iks, reset_scorer

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
    assert "status" in data
    assert "learning_active" in data
    assert data["domain"] == "s2p"


def test_iks_value_in_valid_range():
    response = client.get("/api/s2p/iks")
    assert 0.0 <= response.json()["iks"] <= 100.0


def test_iks_cold_start_is_deterministic_and_unlearned():
    reset_scorer()
    data = get_s2p_iks()

    assert data["decisions"] == 0
    assert data["iks"] == 0.0
    assert data["mean_drift"] == 0.0
    assert data["status"] == "CALIBRATING"
    assert data["learning_active"] is False
    assert "Cold start" in data["interpretation"]
    assert "High institutional knowledge" not in data["interpretation"]


def test_iks_endpoint_reports_cold_start_when_learning_disabled():
    reset_scorer()
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

    reset_scorer()
    data = get_s2p_iks()

    assert data["decisions"] == 0
    assert data["iks"] == 0.0
    assert data["status"] == "CALIBRATING"
    assert "High institutional knowledge" not in data["interpretation"]
