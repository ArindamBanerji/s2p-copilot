from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app
from app.routers import s2p_pvg
from app.services.financial_impact import compute_financial_impact


client = TestClient(app)


def test_financial_impact_endpoint_contract():
    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    data = response.json()
    assert {
        "total_recovered",
        "total_at_risk",
        "total_leakage_prevented",
        "by_category",
        "auto_approve_savings_hours",
        "source",
    }.issubset(data)
    assert set(data["by_category"]) == set(S2PDomainConfig.categories)
    assert data["source"] == "fixture"


def test_financial_impact_auto_approve_savings_uses_count_rule():
    invoices = s2p_pvg.load_invoices()
    expected_count = sum(
        1
        for invoice in invoices
        if invoice.get("verified") is True and invoice.get("ground_truth_action") == "auto_approve"
    )

    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    assert response.json()["auto_approve_savings_hours"] == round(expected_count * 0.25, 2)


def test_financial_impact_empty_verified_invoices_returns_zeros(monkeypatch):
    monkeypatch.setattr(
        s2p_pvg,
        "load_invoices",
        lambda: [
            {
                "category": "price_variance",
                "amount": 100.0,
                "amount_at_risk": 100.0,
                "amount_recovered": None,
                "verified": False,
                "ground_truth_action": "auto_approve",
            }
        ],
    )

    data = s2p_pvg.financial_impact()

    assert data["total_recovered"] == 0.0
    assert data["total_leakage_prevented"] == 0.0
    assert data["auto_approve_savings_hours"] == 0.0
    assert data["by_category"]["price_variance"]["at_risk"] == 100.0


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
