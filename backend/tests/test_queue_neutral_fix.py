"""Tests for queue neutral-factor-substitution fix."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

from app.domains.s2p.config import S2PDomainConfig
from app.main import app


client = TestClient(app)
pytestmark = pytest.mark.usefixtures("preview_graph")


class TestQueueNoNeutralFactors:
    """Queue must not fill missing factors with 0.50."""

    def test_queue_returns_200(self) -> None:
        resp = client.get("/api/s2p/preview/queue")
        assert resp.status_code == 200

    def test_queue_has_exceptions(self) -> None:
        resp = client.get("/api/s2p/preview/queue")
        data = resp.json()
        assert "exceptions" in data
        assert "invoices" in data
        assert data["exceptions"] == data["invoices"]

    def test_no_bulk_neutral_factors(self) -> None:
        """Factor vectors must not be dominated by 0.50 defaults."""
        resp = client.get("/api/s2p/preview/queue")
        data = resp.json()
        items = data.get("exceptions") or data.get("invoices") or []
        neutral_count = 0
        total_count = 0
        for item in items:
            factors = item.get("factors") or item.get("factor_contributions") or {}
            if isinstance(factors, dict):
                for value in factors.values():
                    if isinstance(value, (int, float)):
                        total_count += 1
                        if abs(value - 0.50) < 0.001:
                            neutral_count += 1
        if total_count > 0:
            neutral_ratio = neutral_count / total_count
            assert neutral_ratio < 0.5, (
                f"Queue factors are {neutral_ratio:.0%} neutral (0.50) — "
                "neutral substitution not removed"
            )

        from app.routers.s2p_preview import _score_invoice

        received: dict[str, float] = {}

        class SparseScorer:
            def score_read_only(self, factors: dict[str, float], category: str) -> SimpleNamespace:
                received.update(factors)
                return SimpleNamespace(
                    action="hold_for_review",
                    action_index=1,
                    confidence=0.7,
                    probabilities=[0.1, 0.6, 0.1, 0.1, 0.1],
                )

        sparse = _score_invoice(
            {
                "decision_id": "sparse-decision",
                "invoice_id": "UNKNOWN-SPARSE",
                "supplier_id": "SUP-SPARSE",
                "supplier_name": "Sparse Supplier",
                "category": "price_variance",
                "amount": None,
                "po_number": None,
                "factors": {"match_status": 0.73},
                "ground_truth_action": None,
                "metadata": {},
            },
            SparseScorer(),
        )
        assert received == {"match_status": 0.73}
        assert sparse["factors"] == {"match_status": 0.73}
        assert set(sparse["unavailable_factors"]) == set(S2PDomainConfig.factors) - {"match_status"}
        assert "po_reference" not in sparse
        assert "ground_truth_action" not in sparse
        assert {"po_reference", "ground_truth_action"} <= set(sparse["unavailable_fields"])


class TestInvoiceEnricher:
    """Invoice enricher merges fixture data correctly."""

    def test_enriches_sample_id(self) -> None:
        from app.services.invoice_enricher import enrich_from_fixture

        record = {"invoice_id": "S2P-INV-0001", "po_number": None}
        enriched = enrich_from_fixture(record)
        assert enriched["provenance"] == "sample"
        assert enriched["po_number"] == "PO-20260001"
        assert enriched["ground_truth_action"] in {
            "auto_approve", "hold_for_review", "escalate_to_buyer",
            "reject_duplicate", "request_credit_memo",
        }

    def test_no_enrich_unknown_id(self) -> None:
        from app.services.invoice_enricher import enrich_from_fixture

        record = {"invoice_id": "UNKNOWN-999", "po_number": None}
        enriched = enrich_from_fixture(record)
        assert enriched.get("provenance") != "sample"
        assert enriched["po_number"] is None

    def test_does_not_overwrite_existing(self) -> None:
        from app.services.invoice_enricher import enrich_from_fixture

        record = {"invoice_id": "S2P-INV-0001", "po_number": "EXISTING-PO"}
        enriched = enrich_from_fixture(record)
        assert enriched["po_number"] == "EXISTING-PO"
