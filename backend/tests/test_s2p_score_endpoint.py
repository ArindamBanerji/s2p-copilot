"""
tests/test_s2p_score_endpoint.py — POST /api/s2p/score endpoint tests.

Run from backend/:
    pytest tests/test_s2p_score_endpoint.py -v
"""

import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app
from app.domains.s2p.config import S2PDomainConfig
from app.routers import s2p as s2p_router

client = TestClient(app)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

VALID_REQUEST = {
    "event_id": "E001",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-001",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
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
    assert action in [
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    ]


def test_score_factor_vector_length():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    assert len(data["factor_vector"]) == 7
    assert len(data["factor_names"]) == 7
    assert data["factor_names"] == [
        "match_status",
        "amount_variance_ratio",
        "duplicate_score",
        "supplier_exception_history",
        "payment_terms_impact",
        "commodity_index_correlation",
        "tax_regulatory_compliance",
    ]


def test_score_invalid_category_returns_422():
    bad_request = {**VALID_REQUEST, "category": "lateral_movement"}
    response = client.post("/api/s2p/score", json=bad_request)
    assert response.status_code == 422


def test_score_legacy_category_returns_422():
    bad_request = {**VALID_REQUEST, "category": "supplier_risk"}
    response = client.post("/api/s2p/score", json=bad_request)
    assert response.status_code == 422


def test_score_endpoint_uses_compute_all_factors(monkeypatch):
    calls = []
    known = {name: (idx + 1) / 10 for idx, name in enumerate(S2PDomainConfig.factors)}

    def fake_compute_all_factors(invoice, context=None):
        calls.append((invoice, context))
        return known

    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][1] is None
    assert response.json()["factor_vector"] == [
        known[name] for name in S2PDomainConfig.factors
    ]


def test_score_endpoint_uses_graph_context_when_available(monkeypatch):
    calls = []

    class FakeGraphStore:
        def query_context(self, invoice_id, hops):
            assert invoice_id == VALID_REQUEST["event_id"]
            assert hops == 2
            return [{"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}}]

    def fake_compute_all_factors(invoice, context=None):
        calls.append(context)
        return {name: 0.2 for name in S2PDomainConfig.factors}

    app.state.graph_store = FakeGraphStore()
    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)
    try:
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
    finally:
        del app.state.graph_store

    assert response.status_code == 200
    assert calls == [{"neighbors": [{"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}}]}]


def test_score_endpoint_graph_context_failure_falls_back(monkeypatch):
    calls = []

    class FailingGraphStore:
        def query_context(self, invoice_id, hops):
            raise RuntimeError("graph unavailable")

    def fake_compute_all_factors(invoice, context=None):
        calls.append(context)
        return {name: 0.3 for name in S2PDomainConfig.factors}

    app.state.graph_store = FailingGraphStore()
    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)
    try:
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
    finally:
        del app.state.graph_store

    assert response.status_code == 200
    assert calls == [None]


def test_score_endpoint_uses_fixture_invoice_factors_when_no_graph():
    invoice = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))[0]
    payload = {
        "event_id": invoice["invoice_id"],
        "category": invoice["category"],
        "amount": invoice["amount"],
        "supplier_id": invoice["supplier_id"],
    }

    response = client.post("/api/s2p/score", json=payload)

    assert response.status_code == 200
    assert response.json()["factor_vector"] == [
        invoice["factors"][name] for name in S2PDomainConfig.factors
    ]


def test_score_endpoint_graph_lookup_uses_fixture_invoice_id(monkeypatch):
    invoice = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))[0]
    seen = []

    class FakeGraphStore:
        def query_context(self, invoice_id, hops):
            seen.append((invoice_id, hops))
            return []

    app.state.graph_store = FakeGraphStore()
    try:
        response = client.post(
            "/api/s2p/score",
            json={
                "event_id": invoice["invoice_id"],
                "category": invoice["category"],
                "amount": invoice["amount"],
                "supplier_id": invoice["supplier_id"],
            },
        )
    finally:
        del app.state.graph_store

    assert response.status_code == 200
    assert seen == [(invoice["invoice_id"], 2)]
