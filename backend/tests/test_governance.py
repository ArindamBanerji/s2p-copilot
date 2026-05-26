from __future__ import annotations

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app
from app.models.outcome_receipt import OutcomeReceipt
from app.routers.s2p_data_helpers import load_suppliers
from app.services.receipt_store import get_receipt_store, reset_receipt_store


client = TestClient(app)


def setup_function():
    reset_receipt_store()


def teardown_function():
    reset_receipt_store()


def make_receipt(**overrides) -> OutcomeReceipt:
    payload = {
        "receipt_id": "GOV-R-1",
        "invoice_id": "INV-GOV-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "scored_action": "hold_for_review",
        "confidence": 0.81,
        "factor_vector": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7],
        "category": "price_variance",
        "human_action": "hold_for_review",
        "reward": 1.0,
        "centroid_updated": True,
        "conservation_state_before": "RED",
        "conservation_state_after": "AMBER",
        "verified_count_before": 0,
        "verified_count_after": 1,
    }
    payload.update(overrides)
    return OutcomeReceipt(**payload)


def test_screening_empty_200():
    response = client.get("/api/s2p/governance/compliance-screening")

    assert response.status_code == 200
    data = response.json()
    assert data["total_decisions_screened"] == 0
    assert data["compliance_rate"] == 1.0


def test_screening_with_receipts():
    get_receipt_store().add(make_receipt())

    response = client.get("/api/s2p/governance/compliance-screening")

    assert response.status_code == 200
    data = response.json()
    assert data["total_decisions_screened"] == 1
    assert data["with_gaps"] == 0
    assert data["chain_integrity"]["verified"] is True


def test_screening_detects_gaps():
    receipt = get_receipt_store().add(make_receipt())
    receipt.receipt_hash = ""

    data = client.get("/api/s2p/governance/compliance-screening").json()

    assert data["with_gaps"] == 1
    assert any(gap["issue"] == "missing_hash" for gap in data["gaps"])


def test_screening_detects_incomplete_factors():
    get_receipt_store().add(make_receipt(factor_vector=[0.1, 0.2]))

    data = client.get("/api/s2p/governance/compliance-screening").json()

    assert any(gap["issue"] == "incomplete_factors" for gap in data["gaps"])


def test_has_eu_ai_act():
    data = client.get("/api/s2p/governance/compliance-screening").json()

    assert {"article_14_traceable", "human_oversight_documented", "automated_decision_logged"} <= set(data["eu_ai_act"])


def test_has_sox_readiness():
    data = client.get("/api/s2p/governance/compliance-screening").json()

    assert {"hash_chain_valid", "override_distribution_available", "conservation_proof_available", "score"} <= set(
        data["sox_readiness"]
    )
    assert 0.0 <= data["sox_readiness"]["score"] <= 1.0


def test_gaps_endpoint():
    get_receipt_store().add(make_receipt(factor_vector=[0.1]))

    response = client.get("/api/s2p/governance/compliance-gaps")

    assert response.status_code == 200
    data = response.json()
    assert data["total_gaps"] >= 1
    assert data["issue_summary"]["incomplete_factors"] == 1


def test_conservation_proof():
    get_receipt_store().add(make_receipt())

    response = client.get("/api/s2p/governance/conservation-proof")

    assert response.status_code == 200
    data = response.json()
    assert {"current_state", "total_decisions", "state_transitions", "proof_complete"} <= set(data)
    assert data["state_transitions"]["RED->AMBER"] == 1


def test_existing_compliance_still_200():
    response = client.get("/api/s2p/evidence/compliance")

    assert response.status_code == 200


def test_recommendations_200():
    response = client.get("/api/s2p/governance/rationalization")

    assert response.status_code == 200
    assert response.json()["total_suppliers"] > 0


def test_three_categories_sum():
    data = client.get("/api/s2p/governance/rationalization").json()

    assert data["grow"] + data["maintain"] + data["phase_out"] == data["total_suppliers"]


def test_recommendation_fields():
    recommendation = client.get("/api/s2p/governance/rationalization").json()["recommendations"][0]

    assert {
        "supplier_id",
        "name",
        "recommendation",
        "exception_rate",
        "otif",
        "trend",
        "region",
        "total_invoices",
        "reason",
        "action",
    } <= set(recommendation)


def test_uses_fixture_supplier_ids():
    data = client.get("/api/s2p/governance/rationalization").json()
    supplier_ids = {row["supplier_id"] for row in data["recommendations"]}

    assert "SUP-001" in supplier_ids
    assert all(supplier_id.startswith("SUP-") for supplier_id in supplier_ids)


def test_estimated_savings_shape():
    savings = client.get("/api/s2p/governance/rationalization").json()["estimated_savings"]

    assert {
        "currency",
        "estimated_quarterly_savings",
        "estimated_annual_savings",
        "phase_out_invoice_volume",
        "total_invoice_volume",
        "suppliers_affected",
        "basis",
    } <= set(savings)
    assert savings["currency"] == "USD"


def test_rationalization_uses_fixture_otif_score():
    supplier = load_suppliers()[0]
    data = client.get("/api/s2p/governance/rationalization").json()
    recommendation = next(row for row in data["recommendations"] if row["supplier_id"] == supplier["supplier_id"])

    assert recommendation["otif"] == supplier["otif_score"]


def test_rationalization_uses_fixture_recent_trend():
    supplier = load_suppliers()[0]
    data = client.get("/api/s2p/governance/rationalization").json()
    recommendation = next(row for row in data["recommendations"] if row["supplier_id"] == supplier["supplier_id"])

    assert recommendation["trend"] == supplier["recent_trend"]


def test_rationalization_uses_fixture_total_invoices_for_savings():
    suppliers = {supplier["supplier_id"]: supplier for supplier in load_suppliers()}
    data = client.get("/api/s2p/governance/rationalization").json()
    phase_out = [
        row
        for row in data["recommendations"]
        if row["recommendation"] == "phase_out"
    ]
    savings = data["estimated_savings"]
    expected_annual = round(
        sum(
            suppliers[row["supplier_id"]]["exception_rate"]
            * suppliers[row["supplier_id"]]["total_invoices"]
            * 500
            for row in phase_out
        ),
        2,
    )

    assert savings["suppliers_affected"] == len(phase_out)
    assert savings["estimated_annual_savings"] == expected_annual
    assert savings["phase_out_invoice_volume"] == sum(
        suppliers[row["supplier_id"]]["total_invoices"] for row in phase_out
    )
    if phase_out:
        assert savings["estimated_annual_savings"] > 0
        assert abs(savings["estimated_annual_savings"] - savings["estimated_quarterly_savings"] * 4) <= 0.02


def test_classification_not_all_maintain_due_defaults():
    data = client.get("/api/s2p/governance/rationalization").json()

    assert data["grow"] > 0 or data["phase_out"] > 0


def test_overlap_200():
    response = client.get("/api/s2p/governance/rationalization/overlap")

    assert response.status_code == 200
    data = response.json()
    assert {"overlap_groups", "total_groups", "consolidation_candidates"} <= set(data)


def test_supplier_detail_known():
    response = client.get("/api/s2p/governance/rationalization/supplier/SUP-001")

    assert response.status_code == 200
    data = response.json()
    assert data["supplier"]["supplier_id"] == "SUP-001"
    assert data["recommendation"]["supplier_id"] == "SUP-001"


def test_supplier_detail_recommendation_uses_fixture_fields():
    supplier = load_suppliers()[0]
    response = client.get(f"/api/s2p/governance/rationalization/supplier/{supplier['supplier_id']}")

    assert response.status_code == 200
    recommendation = response.json()["recommendation"]
    assert recommendation["otif"] == supplier["otif_score"]
    assert recommendation["exception_rate"] == supplier["exception_rate"]
    assert recommendation["trend"] == supplier["recent_trend"]
    assert recommendation["total_invoices"] == supplier["total_invoices"]

    if supplier["recent_trend"] == "declining" and supplier["exception_rate"] > 0.15:
        expected = "phase_out"
    elif supplier["recent_trend"] == "declining" and supplier["otif_score"] < 0.75:
        expected = "phase_out"
    elif supplier["otif_score"] >= 0.90 and supplier["exception_rate"] <= 0.10:
        expected = "grow"
    elif supplier["recent_trend"] == "improving":
        expected = "grow"
    else:
        expected = "maintain"
    assert recommendation["recommendation"] == expected


def test_supplier_detail_unknown_404():
    response = client.get("/api/s2p/governance/rationalization/supplier/UNKNOWN")

    assert response.status_code == 404


def test_governance_routes_mounted():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/governance/compliance-screening" in paths
    assert "/api/s2p/governance/compliance-gaps" in paths
    assert "/api/s2p/governance/conservation-proof" in paths
    assert "/api/s2p/governance/rationalization" in paths
    assert "/api/s2p/governance/rationalization/overlap" in paths
    assert "/api/s2p/governance/rationalization/supplier/{supplier_id}" in paths
