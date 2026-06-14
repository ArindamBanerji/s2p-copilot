from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app
from app.services.financial_impact import compute_financial_impact


client = TestClient(app)


def test_financial_impact_endpoint_contract():
    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    data = response.json()
    assert {
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
    }.issubset(data)
    assert "source" not in data


def test_financial_impact_category_endpoint_uses_canonical_categories():
    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    categories = set(response.json()["by_category"])
    assert categories <= set(S2PDomainConfig.categories) or categories == set()


def test_financial_impact_endpoint_no_fixture_source_contract():
    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    assert response.json().get("source") != "fixture"


def test_compute_financial_impact_empty():
    summary = compute_financial_impact([])

    assert summary.total_decisions == 0
    assert summary.verified_decisions == 0
    assert summary.total_recovered == 0.0
    assert summary.recovery_rate == 0.0


def test_compute_financial_impact_basic_receipt_amount_recovered():
    summary = compute_financial_impact(
        [{"decision_id": "d1", "status": "confirmed", "category": "price_variance"}],
        [
            {
                "decision_id": "d1",
                "amount": 1000.0,
                "amount_at_risk": 100.0,
                "amount_recovered": 80.0,
                "supplier_name": "Acme",
                "category": "price_variance",
            }
        ],
    )

    assert summary.verified_decisions == 1
    assert summary.total_amount == 1000.0
    assert summary.total_at_risk == 100.0
    assert summary.total_recovered == 80.0
    assert summary.net_savings == 80.0


def test_compute_financial_impact_excludes_unverified():
    summary = compute_financial_impact(
        [
            {"decision_id": "d1", "status": "pending", "amount_recovered": 100.0},
            {"decision_id": "d2", "status": "confirmed", "amount_recovered": 25.0},
        ]
    )

    assert summary.total_decisions == 2
    assert summary.verified_decisions == 1
    assert summary.total_recovered == 25.0


def test_compute_financial_impact_by_supplier():
    summary = compute_financial_impact(
        [
            {"decision_id": "d1", "status": "confirmed", "supplier_name": "Acme", "amount_recovered": 10.0},
            {"decision_id": "d2", "status": "overridden", "supplier_name": "Acme", "amount_recovered": 15.0},
            {"decision_id": "d3", "status": "confirmed", "supplier_name": "Beta", "amount_recovered": 5.0},
        ]
    )

    assert summary.by_supplier["Acme"]["count"] == 2
    assert summary.by_supplier["Acme"]["recovered"] == 25.0
    assert summary.by_supplier["Beta"]["recovered"] == 5.0


def test_compute_financial_impact_by_category():
    summary = compute_financial_impact(
        [
            {"decision_id": "d1", "status": "confirmed", "category": "price_variance", "amount_recovered": 10.0},
            {"decision_id": "d2", "status": "confirmed", "category": "duplicate_invoice", "amount_recovered": 30.0},
        ]
    )

    assert summary.by_category["price_variance"]["recovered"] == 10.0
    assert summary.by_category["duplicate_invoice"]["recovered"] == 30.0


def test_compute_financial_impact_recovery_rate():
    summary = compute_financial_impact(
        [{"decision_id": "d1", "status": "confirmed", "amount_at_risk": 200.0, "amount_recovered": 50.0}]
    )

    assert summary.recovery_rate == 0.25


def test_compute_financial_impact_missing_receipt_uses_decision_values():
    summary = compute_financial_impact(
        [
            {
                "decision_id": "d1",
                "status": "confirmed",
                "amount": 500.0,
                "amount_at_risk": 40.0,
                "amount_recovered": 35.0,
                "supplier_name": "Acme",
            }
        ],
        [],
    )

    assert summary.missing_receipts == 1
    assert summary.total_amount == 500.0
    assert summary.total_recovered == 35.0


def test_compute_financial_impact_null_amounts_are_zero():
    summary = compute_financial_impact(
        [
            {
                "decision_id": "d1",
                "status": "confirmed",
                "amount": None,
                "amount_at_risk": None,
                "amount_recovered": None,
            }
        ]
    )

    assert summary.total_amount == 0.0
    assert summary.total_at_risk == 0.0
    assert summary.total_recovered == 0.0


def test_compute_financial_impact_sparse_twelve_verified():
    decisions = [
        {
            "decision_id": f"d{index}",
            "status": "confirmed",
            "category": "price_variance",
            "amount_at_risk": 10.0,
            "amount_recovered": 4.0,
        }
        for index in range(12)
    ]
    summary = compute_financial_impact(decisions)

    assert summary.verified_decisions == 12
    assert summary.total_at_risk == 120.0
    assert summary.total_recovered == 48.0


def test_compute_financial_impact_to_dict_shape():
    summary = compute_financial_impact(
        [{"decision_id": "d1", "status": "confirmed", "category": "price_variance", "amount_recovered": 7.0}]
    )

    data = summary.to_dict()
    assert data["verified_decisions"] == 1
    assert data["by_category"]["price_variance"]["recovered"] == 7.0
