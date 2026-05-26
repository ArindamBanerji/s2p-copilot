from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app
from app.routers import s2p_pvg


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
