"""Tests for shared supplier profile provider and endpoint fixes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


class TestSupplierProfileProvider:
    """Provider correctly maps fixture fields and merges live data."""

    def test_fixture_otif_mapped(self) -> None:
        """otif_score from fixture maps to otif in profile."""
        from app.services.supplier_profile_provider import build_profiles

        profiles = build_profiles(verified_decisions=[], fixture_path=None)
        for profile in profiles:
            if profile.get("provenance") == "sample_fallback":
                assert profile.get("otif") is not None

    def test_trend_not_parsed_as_float(self) -> None:
        """Textual recent_trend like 'declining' must not become null."""
        from app.services.supplier_profile_provider import build_profiles

        profiles = build_profiles(verified_decisions=[], fixture_path=None)
        fallback = [p for p in profiles if p.get("provenance") == "sample_fallback"]
        assert fallback
        assert all(isinstance(p.get("trend_direction"), str) for p in fallback)

    def test_unavailable_fields_explicit(self) -> None:
        """Region and financial_health_trend should not be null."""
        from app.services.supplier_profile_provider import build_profiles

        profiles = build_profiles(verified_decisions=[], fixture_path=None)
        for profile in profiles:
            assert "region" not in profile
            assert "financial_health_trend" not in profile
            assert profile["region_available"] is False
            assert profile["financial_health_trend_available"] is False
            assert profile["field_provenance"]["region"] == "unavailable"
            assert profile["field_provenance"]["financial_health_trend"] == "unavailable"

    def test_live_values_replace_fixture_baseline_without_double_counting(self) -> None:
        """Verified graph invoices take precedence over fixture baseline metrics."""
        from app.services.supplier_profile_provider import build_profiles

        profiles = build_profiles([
            {
                "decision_id": "live-1",
                "domain": "s2p",
                "status": "verified",
                "supplier_id": "SUP-001",
                "supplier_name": "Aster Live",
                "invoice_id": "INV-LIVE-1",
                "category": "industrial chemicals",
                "amount": 250.0,
                "actual_action": "manual_review",
                "otif_score": 0.73,
                "lead_time_days": 12,
                "created_at": "2026-10-11T12:00:00Z",
            }
        ])
        profile = next(p for p in profiles if p["supplier_id"] == "SUP-001")
        assert profile["provenance"] == "graph"
        assert profile["invoice_count"] == 1
        assert profile["exception_rate"] == 1.0
        assert profile["otif"] == 0.73
        assert profile["field_provenance"]["invoice_count"] == "graph"

    def test_runtime_supplier_in_full_but_not_preview(self) -> None:
        """Runtime-only suppliers stay in the portfolio and out of the curated preview."""
        from app.services.supplier_profile_provider import build_profiles, preview_profiles

        profiles = build_profiles([
            {
                "decision_id": "runtime-1",
                "domain": "s2p",
                "status": "verified",
                "supplier_id": "RUNTIME-ONLY",
                "supplier_name": "Runtime Supplier",
                "invoice_id": "INV-RUNTIME-1",
                "category": "services",
                "actual_action": "auto_approve",
            }
        ])
        assert any(p["supplier_id"] == "RUNTIME-ONLY" for p in profiles)
        curated = preview_profiles(profiles)
        assert len(curated) == 10
        assert all(p["supplier_id"] != "RUNTIME-ONLY" for p in curated)


class TestSuppliersEndpoint:
    """Full portfolio endpoint returns correct data."""

    def test_suppliers_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/s2p/suppliers")
        assert resp.status_code == 200

    def test_suppliers_has_count(self, client: TestClient) -> None:
        """count field must not be null."""
        data = client.get("/api/s2p/suppliers").json()
        count = data.get("count") or data.get("total")
        assert count is not None
        assert isinstance(count, int)
        assert count > 0

    def test_no_numeric_parse_nulls(self, client: TestClient) -> None:
        """No fallback supplier loses its categorical trend to numeric parsing."""
        data = client.get("/api/s2p/suppliers").json()
        suppliers = data.get("suppliers") or data.get("items") or []
        fallback = [s for s in suppliers if s.get("provenance") == "sample_fallback"]
        assert all(isinstance(s.get("trend_direction"), str) for s in fallback)


class TestPreviewSuppliersEndpoint:
    """Preview endpoint returns curated 10-supplier view."""

    def test_preview_suppliers_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/s2p/preview/suppliers")
        assert resp.status_code == 200

    def test_preview_has_suppliers(self, client: TestClient) -> None:
        data = client.get("/api/s2p/preview/suppliers").json()
        suppliers = data.get("suppliers") or data.get("items") or []
        assert len(suppliers) == 10

    def test_preview_otif_not_null(self, client: TestClient) -> None:
        """OTIF should come from fixture otif_score, not be null."""
        data = client.get("/api/s2p/preview/suppliers").json()
        suppliers = data.get("suppliers") or data.get("items") or []
        null_otif = sum(1 for supplier in suppliers if supplier.get("otif") is None)
        assert null_otif < len(suppliers), (
            f"{null_otif}/{len(suppliers)} suppliers have null OTIF — "
            "fixture otif_score not mapped"
        )
