"""
tests/test_s2p_learning_gate.py — S2P Learning Activation Gate tests.

Run from backend/:
    pytest tests/test_s2p_learning_gate.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_s2p_learning_gate_returns_200():
    resp = client.get("/api/s2p/learning-gate")
    assert resp.status_code == 200


def test_s2p_learning_gate_returns_valid_status():
    data = client.get("/api/s2p/learning-gate").json()
    assert data["status"] in ["GREEN", "AMBER", "RED"]
    assert isinstance(data["learning_active"], bool)


def test_s2p_learning_gate_amber_when_cold():
    """Cold start (0 decisions) must be AMBER, not GREEN."""
    data = client.get("/api/s2p/learning-gate").json()
    # Cold start: 0 decisions < 50 threshold -> AMBER or RED
    if data["verified_decisions"] < 50:
        assert data["status"] in ["AMBER", "RED"]
        assert data["learning_active"] == False


def test_s2p_learning_gate_has_thresholds():
    """Thresholds must be present and correct."""
    data = client.get("/api/s2p/learning-gate").json()
    assert "thresholds" in data
    t = data["thresholds"]
    assert t["min_verified_decisions"] == 50
    assert t["min_override_precision"] == 0.40


def test_evaluate_s2p_learning_gate_green_when_conditions_met():
    """Unit test: gate opens GREEN when all conditions met."""
    from app.services.s2p_learning_gate import evaluate_s2p_learning_gate
    result = evaluate_s2p_learning_gate(
        verified_decisions=75,
        override_precision=0.65,
        sigma_max=0.10,
    )
    assert result.status == "GREEN"
    assert result.learning_active == True


def test_evaluate_s2p_learning_gate_amber_low_decisions():
    """Unit test: gate stays AMBER with insufficient decisions."""
    from app.services.s2p_learning_gate import evaluate_s2p_learning_gate
    result = evaluate_s2p_learning_gate(
        verified_decisions=30,
        override_precision=0.80,
        sigma_max=0.10,
    )
    assert result.status == "AMBER"
    assert result.learning_active == False


def test_evaluate_s2p_learning_gate_red_high_sigma():
    """Unit test: gate RED when sigma exceeds threshold."""
    from app.services.s2p_learning_gate import evaluate_s2p_learning_gate
    result = evaluate_s2p_learning_gate(
        verified_decisions=100,
        override_precision=0.80,
        sigma_max=0.30,  # above 0.25 RED threshold
    )
    assert result.status == "RED"
    assert result.learning_active == False
