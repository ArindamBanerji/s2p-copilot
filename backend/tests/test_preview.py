"""S2P preview endpoint contract tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app


client = TestClient(app)
ENGINE_VERSION = "v0.7.23"


def _queue():
    return client.get("/api/s2p/preview/queue").json()


def test_preview_queue_returns_exceptions():
    data = _queue()
    assert data["total"] == 50
    assert len(data["exceptions"]) == 10
    assert all("invoice_id" in row for row in data["exceptions"])


def test_preview_queue_has_engine_version():
    assert _queue()["engine_version"] == ENGINE_VERSION


def test_preview_queue_confidence_in_range():
    rows = _queue()["exceptions"]
    assert all(0.0 <= row["confidence"] <= 1.0 for row in rows)
    assert [row["confidence"] for row in rows] == sorted(
        [row["confidence"] for row in rows],
        reverse=True,
    )


def test_preview_queue_categories_match_config():
    categories = set(S2PDomainConfig.categories)
    assert {row["category"] for row in _queue()["exceptions"]}.issubset(categories)


def test_preview_queue_auto_approve_rate():
    data = _queue()
    assert 0.0 <= data["auto_approve_rate"] <= 1.0
    assert 0.0 <= data["confidence_avg"] <= 1.0


def test_preview_conservation_status():
    data = client.get("/api/s2p/preview/conservation").json()
    assert data["status"] == "GREEN"
    assert data["passed"] is True


def test_preview_conservation_is_illustration():
    data = client.get("/api/s2p/preview/conservation").json()
    assert data["source"] == "illustration"
    assert data["verified_decisions"] == 1000
    assert data["accuracy"] == 0.84


def test_preview_conservation_penalty_ratio():
    data = client.get("/api/s2p/preview/conservation").json()
    assert data["penalty_ratio"] == 5.0
    assert data["auto_approve_rate"] == 0.45


def test_preview_suppliers_default_returns_all():
    response = client.get("/api/s2p/preview/suppliers")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 10
    assert data["showing"] == 10
    assert len(data["suppliers"]) == 10


def test_preview_suppliers_limit_is_explicit():
    data = client.get("/api/s2p/preview/suppliers?limit=2").json()
    assert data["total"] == 10
    assert data["showing"] == 2
    assert len(data["suppliers"]) == 2


def test_preview_suppliers_have_profiles():
    supplier = client.get("/api/s2p/preview/suppliers").json()["suppliers"][0]
    for key in (
        "supplier_id",
        "name",
        "exception_rate",
        "otif_score",
        "category",
        "avg_invoice_amount",
        "recent_trend",
    ):
        assert key in supplier


def test_preview_suppliers_exception_rates():
    suppliers = client.get("/api/s2p/preview/suppliers").json()["suppliers"]
    assert all(0.0 <= supplier["exception_rate"] <= 1.0 for supplier in suppliers)
    assert all(0.0 <= supplier["otif_score"] <= 1.0 for supplier in suppliers)


def test_preview_queue_uses_s2p_config():
    data = _queue()
    action_names = set(S2PDomainConfig.actions)
    factor_names = list(S2PDomainConfig.factors)
    for row in data["exceptions"]:
        assert row["scored_action"] in action_names
        assert list(row["factors"]) == factor_names


def test_preview_doesnt_break_existing_score():
    payload = {
        "event_id": "PREVIEW-SCORE-001",
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-001",
        "match_status": 0.95,
        "amount_variance_ratio": 0.05,
        "duplicate_score": 0.02,
        "supplier_exception_history": 0.03,
        "payment_terms_impact": 0.50,
        "commodity_index_correlation": 0.80,
        "tax_regulatory_compliance": 0.95,
    }
    response = client.post("/api/s2p/score", json=payload)
    assert response.status_code == 200
    assert response.json()["action"] in S2PDomainConfig.actions


def test_all_endpoints_return_engine_version():
    for path in (
        "/api/s2p/preview/queue",
        "/api/s2p/preview/conservation",
        "/api/s2p/preview/suppliers",
        "/api/s2p/preview/compounding",
        "/api/s2p/preview/config",
    ):
        assert client.get(path).json()["engine_version"] == ENGINE_VERSION


def test_preview_queue_scores_with_live_or_centroid_scorer():
    row = _queue()["exceptions"][0]
    assert row["scored_action"] in S2PDomainConfig.actions
    assert row["confidence"] > 0
    assert len(row["probabilities"]) == len(S2PDomainConfig.actions)
