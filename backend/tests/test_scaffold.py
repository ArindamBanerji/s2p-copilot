"""
tests/test_scaffold.py — S2P Copilot Step 0 smoke tests.

Verifies: health endpoint, framework imports, GAE importability.
Run from backend/:
    pytest tests/test_scaffold.py -v
"""

import sys
import os
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_health_endpoint_reports_offline_graph():
    """Offline tests retain service metadata without claiming AGE readiness."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["status"] == "error"
    assert response.json()["version"] == app.version
    assert response.json()["service"] == "s2p-copilot"


@pytest.mark.parametrize("path", ["/health", "/api/health"])
@pytest.mark.parametrize("connected", [True, False])
def test_health_merges_shared_readiness_and_service_fields(monkeypatch, path, connected):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr(app.state, "graph_health_config", SimpleNamespace(
        backend="age", graph="soc_graph", dsn="test-only", source_keys=(),
    ))
    monkeypatch.setattr(app.state, "graph_store", SimpleNamespace(
        health_check=lambda: {"connected": connected},
    ))
    response = TestClient(app).get(path)
    body = response.json()
    assert response.status_code == (200 if connected else 503)
    assert body["ready"] is connected
    assert body["graph_connected"] is connected
    assert body["service"] == "s2p-copilot"
    assert body["version"] == app.version
    assert body["domain"] == "s2p"
    assert body["graph_status"]["components"]["decisions"] == ("ready" if connected else "unavailable")


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
