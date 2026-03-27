"""
tests/test_scaffold.py — S2P Copilot Step 0 smoke tests.

Verifies: health endpoint, framework imports, GAE importability.
Run from backend/:
    pytest tests/test_scaffold.py -v
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_health_endpoint_returns_ok():
    """GET /health returns 200 with service='s2p-copilot'."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "s2p-copilot"


def test_framework_discipline_enforced():
    """Core framework modules import without error."""
    import importlib
    from app.framework import ols_status, checkpoint, agent
    assert ols_status is not None
    assert checkpoint is not None
    assert agent is not None


def test_gae_importable():
    """GAE 0.7.20+ is importable with required symbols."""
    import gae
    from gae import DiagonalKernel, KernelType, build_profile_scorer
    assert gae.__version__ >= "0.7.20"
