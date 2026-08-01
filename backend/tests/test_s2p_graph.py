"""
tests/test_s2p_graph.py — S2P graph write-back tests.

Run from backend/:
    pytest tests/test_s2p_graph.py -v
"""

from pathlib import Path

from copilot_sdk.graph import InMemoryGraphStore


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
    store = InMemoryGraphStore(domain="s2p")
    result = store.get_decision("NONEXISTENT", domain="s2p")
    assert result is None


def test_s2p_decision_read_uses_graphstore():
    store = InMemoryGraphStore(domain="s2p")
    decision_id = store.write_decision(
        domain="s2p",
        category="price_variance",
        action="hold_for_review",
        confidence=0.9,
        factors={"amount_variance_ratio": 0.2},
    )

    decision = store.get_decision(decision_id, domain="s2p")

    assert decision is not None
    assert decision["domain"] == "s2p"


def test_legacy_graph_module_not_imported_in_production():
    app_root = Path(__file__).resolve().parents[1] / "app"
    matches = []
    for source_file in app_root.rglob("*.py"):
        source = source_file.read_text(encoding="utf-8")
        if "domains.s2p.graph" in source or "import app.domains.s2p.graph" in source:
            matches.append(str(source_file))

    assert matches == []
