from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.models.outcome_receipt import OutcomeReceipt
from app.services.receipt_store import get_receipt_store, reset_receipt_store


client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_receipts() -> Iterator[None]:
    reset_receipt_store()
    yield
    reset_receipt_store()


def _receipt(invoice_id: str = "demo-s2p-0001") -> OutcomeReceipt:
    vector = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.5]
    return OutcomeReceipt(
        receipt_id="R-PW-1",
        decision_id="D-PW-1",
        invoice_id=invoice_id,
        timestamp="2026-10-17T12:00:00Z",
        scored_action="hold_for_review",
        confidence=0.82,
        factor_vector=vector,
        factors=dict(zip(S2PDomainConfig.factors, vector, strict=True)),
        category="price_variance",
        human_action="hold_for_review",
        is_correct=True,
        amount=1250.0,
        conservation_state_before="GREEN",
        conservation_state_after="GREEN",
        verified_count_before=10,
        verified_count_after=11,
    )


def test_centroid_explorer_invalid_category_returns_404() -> None:
    response = client.get(
        "/api/s2p/centroid-explorer",
        params={"category": "NONEXISTENT_CATEGORY"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown category: NONEXISTENT_CATEGORY"}


def test_centroid_explorer_missing_decision_returns_404() -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store

    response = client.get(
        "/api/s2p/centroid-explorer/explain",
        params={"decision_id": "NONEXISTENT_DECISION"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Decision not found: NONEXISTENT_DECISION"}


def test_financial_impact_invalid_query_category_returns_400() -> None:
    response = client.get(
        "/api/s2p/financial-impact",
        params={"category": "INVALID"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Invalid category",
        "allowed_categories": list(S2PDomainConfig.categories),
    }


def test_centroid_cell_valid_category() -> None:
    """Happy-path: valid category and action return centroid data."""
    response = client.get("/api/s2p/centroid/price_variance/auto_approve")

    assert response.status_code == 200
    assert response.json()["category"] == "price_variance"


def test_financial_impact_valid_category() -> None:
    """Happy-path: valid category returns financial data."""
    response = client.get(
        "/api/s2p/financial-impact",
        params={"category": "price_variance"},
    )

    assert response.status_code == 200
    assert "total_decisions" in response.json()


def test_centroid_cell_invalid_action() -> None:
    """Invalid action returns 404."""
    response = client.get(
        "/api/s2p/centroid/price_variance/nonexistent_action",
    )

    assert response.status_code == 404


def test_receipt_valid_invoice() -> None:
    get_receipt_store().add(_receipt())

    response = client.get("/api/s2p/evidence/receipts/demo-s2p-0001")

    assert response.status_code == 200
    assert response.json()["outcome"] == "correct"


def test_receipt_invalid_invoice() -> None:
    response = client.get("/api/s2p/evidence/receipts/NONEXISTENT-9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Invoice not found: NONEXISTENT-9999"}


def test_receipt_response_shape() -> None:
    get_receipt_store().add(_receipt())

    payload = client.get("/api/s2p/evidence/receipts/demo-s2p-0001").json()

    assert {
        "invoice_id",
        "outcome",
        "decision_id",
        "factors",
        "pd_audit",
        "timestamp",
        "amount",
        "receipts",
    } <= set(payload)
    assert len(payload["factors"]) == S2PDomainConfig.n_factors
    assert payload["amount"] == 1250.0


def test_supplier_enrichment_uses_cached_profile_without_graph_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    class CachedProfiles:
        def get(self, key: str, *, item: str | None = None) -> Any:
            assert (key, item) == ("supplier_profiles", "profiles")
            return [{"supplier_id": "SUP-001", "source": "graph", "invoice_count": 12}]

    monkeypatch.setattr(app.state, "materializer", CachedProfiles(), raising=False)
    started = time.perf_counter()

    response = client.get("/api/s2p/supplier-enrichment/SUP-001")

    assert response.status_code == 200
    assert response.json()["supplier_id"] == "SUP-001"
    assert time.perf_counter() - started < 2.0
