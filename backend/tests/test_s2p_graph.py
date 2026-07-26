"""
tests/test_s2p_graph.py — S2P graph write-back tests.

Uses unittest.mock to avoid real Neo4j dependency.
Run from backend/:
    pytest tests/test_s2p_graph.py -v
"""

import sys
import os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.graph import get_s2p_decision


def _make_driver(return_none=False):
    """Build a mock Neo4j driver whose session().run().single() returns a record or None."""
    if return_none:
        single_rv = None
    else:
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: "S2P-E001-2026-01-01T00-00-00" if key == "decision_id" else None
        single_rv = mock_record
    mock_result = MagicMock()
    mock_result.single.return_value = single_rv
    mock_session = MagicMock()
    mock_session.run.return_value = mock_result
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_driver = MagicMock()
    mock_driver.session.return_value = mock_session
    return mock_driver


def test_score_endpoint_includes_decision_id():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    response = client.post("/api/s2p/score", json={
        "event_id": "E099", "category": "price_variance",
        "amount": 1000.0, "supplier_id": "SUP-099",
        "match_status": 0.9,
        "amount_variance_ratio": 0.08,
        "duplicate_score": 0.04,
        "supplier_exception_history": 0.05,
        "payment_terms_impact": 0.48,
        "commodity_index_correlation": 0.76,
        "tax_regulatory_compliance": 0.90,
    })
    assert response.status_code == 200
    data = response.json()
    assert "decision_id" in data
    assert data["decision_id"].startswith("S2P-")


def test_get_s2p_decision_returns_none_when_not_found():
    mock_driver = _make_driver(return_none=True)
    result = get_s2p_decision(mock_driver, "NONEXISTENT")
    assert result is None
