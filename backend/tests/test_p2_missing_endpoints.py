from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app


client = TestClient(app)


def _write_decision() -> str:
    factors = {name: 0.5 for name in S2PDomainConfig.factors}
    return str(
        app.state.graph_store.write_decision(
            domain="s2p",
            category="price_variance",
            action="auto_approve",
            confidence=0.8,
            factors=factors,
            metadata={
                "invoice_id": "P2-INV-001",
                "factor_vector": list(factors.values()),
            },
        )
    )


def test_situation_endpoint_shape_and_unknown_default() -> None:
    decision_id = _write_decision()

    response = client.get(f"/api/s2p/situation/{decision_id}?max_depth=3")
    assert response.status_code == 200
    assert {
        "decision_id",
        "category",
        "context_chain",
        "nl_explanation",
        "confidence",
        "factors_used",
        "traversal_depth",
        "context_available",
        "warnings",
        "missing_variables",
        "provenance",
    }.issubset(response.json())

    unknown = client.get("/api/s2p/situation/UNKNOWN?max_depth=3")
    assert unknown.status_code == 200
    assert unknown.json()["context_chain"] == []


def test_process_context_endpoint_shape_and_unknown_default() -> None:
    response = client.get("/api/s2p/insight/process-context/S2P-INV-0001")
    assert response.status_code == 200
    assert {
        "invoice_id",
        "total_cycle_time_hours",
        "activities",
        "activity_timeline",
        "bottleneck",
        "source",
        "engine",
        "narrative",
    }.issubset(response.json())

    unknown = client.get("/api/s2p/insight/process-context/UNKNOWN")
    assert unknown.status_code == 200
    assert unknown.json()["activities"] == []


def test_centroid_endpoint_shape_and_unknown_default() -> None:
    response = client.get("/api/s2p/centroid/price_variance/auto_approve")
    assert response.status_code == 200
    required = {
        "category",
        "category_index",
        "action",
        "action_index",
        "factor_names",
        "centroid_vector",
        "source",
        "read_only",
    }
    assert required.issubset(response.json())

    unknown = client.get("/api/s2p/centroid/UNKNOWN/UNKNOWN")
    assert unknown.status_code == 200
    assert required.issubset(unknown.json())
    assert unknown.json()["centroid_vector"] == []


def test_centroid_explanation_endpoint_shape_and_unknown_default() -> None:
    decision_id = _write_decision()
    response = client.get(f"/api/s2p/centroid/explain/{decision_id}")
    assert response.status_code == 200
    required = {
        "decision_id",
        "category",
        "recommended_action",
        "closest_action",
        "closest_matches_recommendation",
        "factor_names",
        "factor_contributions",
        "centroid_distances",
        "summary",
        "dk_status",
        "p39_evidence",
        "read_only",
    }
    assert required.issubset(response.json())

    unknown = client.get("/api/s2p/centroid/explain/UNKNOWN")
    assert unknown.status_code == 200
    assert required.issubset(unknown.json())
    assert unknown.json()["factor_contributions"] == []


def test_centroid_drift_endpoint_shape_and_unknown_default() -> None:
    response = client.get("/api/s2p/centroid/drift/price_variance/auto_approve")
    assert response.status_code == 200
    required = {"category", "action", "supported", "reason", "points"}
    assert required.issubset(response.json())

    unknown = client.get("/api/s2p/centroid/drift/UNKNOWN/UNKNOWN")
    assert unknown.status_code == 200
    assert required.issubset(unknown.json())
    assert unknown.json()["points"] == []


def test_financial_impact_endpoint_shape_and_unknown_default() -> None:
    response = client.get("/api/s2p/financial-impact/price_variance")
    assert response.status_code == 200
    required = {
        "total_decisions",
        "verified_decisions",
        "total_amount",
        "total_at_risk",
        "total_recovered",
        "net_savings",
        "recovery_rate",
        "missing_receipts",
        "by_supplier",
        "by_category",
        "category",
        "allowed_categories",
    }
    assert required.issubset(response.json())

    unknown = client.get("/api/s2p/financial-impact/UNKNOWN")
    assert unknown.status_code == 200
    assert required.issubset(unknown.json())
    assert unknown.json()["total_decisions"] == 0


def test_supplier_profile_endpoint_shape_and_unknown_default() -> None:
    response = client.get("/api/s2p/suppliers/SUP-001/profile")
    assert response.status_code == 200
    required = {
        "supplier_id",
        "supplier_name",
        "exception_rate",
        "exception_rate_trend",
        "otif",
        "otif_by_quarter",
        "avg_lead_time_days",
        "lead_time_by_quarter",
        "invoice_count",
        "last_invoice_date",
        "pricing_trend",
        "categories",
        "last_updated",
        "source",
    }
    assert required.issubset(response.json())

    unknown = client.get("/api/s2p/suppliers/UNKNOWN/profile")
    assert unknown.status_code == 200
    assert required.issubset(unknown.json())
    assert unknown.json()["invoice_count"] == 0


def test_supplier_history_endpoint_shape_and_unknown_default() -> None:
    response = client.get("/api/s2p/suppliers/SUP-001/history")
    assert response.status_code == 200
    assert {"events", "total"}.issubset(response.json())

    unknown = client.get("/api/s2p/suppliers/UNKNOWN/history")
    assert unknown.status_code == 200
    assert unknown.json() == {"events": [], "total": 0}


def test_supplier_heatmap_endpoint_shape_and_unknown_default() -> None:
    response = client.get("/api/s2p/suppliers/SUP-001/heatmap")
    assert response.status_code == 200
    assert {"supplier_id", "cells"}.issubset(response.json())
    assert all({"day", "hour", "count"}.issubset(cell) for cell in response.json()["cells"])

    unknown = client.get("/api/s2p/suppliers/UNKNOWN/heatmap")
    assert unknown.status_code == 200
    assert unknown.json()["cells"] == []
