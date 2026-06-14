from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app
from app.routers import financial_router
from app.services.financial_impact import FinancialSummary


client = TestClient(app)


class FakeGraphStore:
    domain = "s2p"

    def __init__(self, decisions: list[dict] | None = None):
        self.decisions = list(decisions or [])
        self.calls: list[tuple] = []

    def get_all_decisions(self, *args):
        self.calls.append(args)
        return list(self.decisions)


class FakeReceiptStore:
    def __init__(self, receipts: list[dict] | None = None):
        self.receipts = list(receipts or [])

    def get_chain(self, limit: int = 100):
        return list(self.receipts)[-limit:]


def _set_financial_state(monkeypatch, decisions: list[dict], receipts: list[dict] | None = None) -> None:
    graph_store = FakeGraphStore(decisions)
    app.state.graph_store = graph_store
    app.state.scorer = SimpleNamespace(graph_store=graph_store)
    monkeypatch.setattr(financial_router, "get_receipt_store", lambda: FakeReceiptStore(receipts or []))


def test_financial_impact_summary_returns_p28_fields(monkeypatch):
    _set_financial_state(
        monkeypatch,
        [
            {
                "decision_id": "D1",
                "status": "confirmed",
                "category": "price_variance",
                "amount": 1000.0,
                "amount_at_risk": 100.0,
                "amount_recovered": 80.0,
                "supplier_name": "Acme",
                "created_at": 1700000000.0,
            }
        ],
    )

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
    assert data["verified_decisions"] == 1
    assert data["total_recovered"] == 80.0


def test_financial_impact_empty_data_returns_zero_values(monkeypatch):
    _set_financial_state(monkeypatch, [])

    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    data = response.json()
    assert data["total_decisions"] == 0
    assert data["verified_decisions"] == 0
    assert data["total_recovered"] == 0.0
    assert data["by_category"] == {}


def test_financial_impact_trend_returns_points_array(monkeypatch):
    _set_financial_state(
        monkeypatch,
        [
            {
                "decision_id": "D1",
                "status": "confirmed",
                "category": "price_variance",
                "amount": 1000.0,
                "amount_at_risk": 100.0,
                "amount_recovered": 80.0,
                "created_at": 1700000000.0,
            }
        ],
    )

    response = client.get("/api/s2p/financial-impact/trend")

    assert response.status_code == 200
    data = response.json()
    assert data["window_weeks"] == 12
    assert isinstance(data["points"], list)
    assert data["points"]
    assert data["points"][0]["week"]


def test_financial_impact_trend_attributes_receipt_to_decision_week(monkeypatch):
    _set_financial_state(
        monkeypatch,
        [
            {
                "decision_id": "D1",
                "status": "confirmed",
                "category": "price_variance",
                "created_at": "2024-01-03T12:00:00+00:00",
            }
        ],
        [
            {
                "decision_id": "D1",
                "category": "price_variance",
                "amount": 1000.0,
                "amount_at_risk": 100.0,
                "amount_recovered": 80.0,
                "timestamp": "2024-01-10T12:00:00+00:00",
            }
        ],
    )

    response = client.get("/api/s2p/financial-impact/trend")

    assert response.status_code == 200
    points = response.json()["points"]
    assert [point["week"] for point in points] == ["2024-W01"]
    assert points[0]["total_recovered"] == 80.0
    assert points[0]["missing_receipts"] == 0
    assert response.json()["totals"]["total_recovered"] == 80.0


def test_financial_impact_trend_is_not_captured_as_category(monkeypatch):
    _set_financial_state(monkeypatch, [])

    response = client.get("/api/s2p/financial-impact/trend")

    assert response.status_code == 200
    assert "points" in response.json()


def test_financial_impact_trend_without_timestamps_returns_empty_series(monkeypatch):
    _set_financial_state(
        monkeypatch,
        [{"decision_id": "D1", "status": "confirmed", "category": "price_variance"}],
    )

    response = client.get("/api/s2p/financial-impact/trend")

    assert response.status_code == 200
    data = response.json()
    assert data["as_of"] is None
    assert data["points"] == []
    assert data["totals"]["total_decisions"] == 0


def test_financial_impact_category_filters_decisions_and_receipts(monkeypatch):
    _set_financial_state(
        monkeypatch,
        [
            {"decision_id": "D1", "status": "confirmed", "category": "price_variance"},
            {"decision_id": "D2", "status": "confirmed", "category": "contract_gap"},
        ],
        [
            {
                "decision_id": "D1",
                "category": "price_variance",
                "amount": 500.0,
                "amount_at_risk": 50.0,
                "amount_recovered": 45.0,
            },
            {
                "decision_id": "D2",
                "category": "contract_gap",
                "amount": 800.0,
                "amount_at_risk": 100.0,
                "amount_recovered": 10.0,
            },
        ],
    )

    response = client.get("/api/s2p/financial-impact/price_variance")

    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "price_variance"
    assert data["allowed_categories"] == list(S2PDomainConfig.categories)
    assert data["total_recovered"] == 45.0
    assert set(data["by_category"]) == {"price_variance"}


def test_financial_impact_invalid_category_returns_allowed_categories(monkeypatch):
    _set_financial_state(monkeypatch, [])

    response = client.get("/api/s2p/financial-impact/not_a_category")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "allowed_categories" in detail
    assert detail["allowed_categories"] == list(S2PDomainConfig.categories)


def test_financial_impact_endpoints_have_response_models():
    endpoints = {
        (getattr(route, "path", ""), getattr(route.endpoint, "__name__", "")): route
        for route in app.routes
    }

    assert endpoints[("/api/s2p/financial-impact", "financial_impact")].response_model is not None
    assert endpoints[("/api/s2p/financial-impact/trend", "financial_impact_trend")].response_model is not None
    assert endpoints[("/api/s2p/financial-impact/{category}", "financial_impact_category")].response_model is not None


def test_financial_impact_uses_p28_compute_function(monkeypatch):
    _set_financial_state(monkeypatch, [{"decision_id": "D1", "status": "confirmed"}])
    calls = []

    def fake_compute(decisions, receipts=None):
        calls.append((list(decisions), list(receipts or [])))
        return FinancialSummary(total_decisions=99, verified_decisions=88, total_recovered=77.0)

    monkeypatch.setattr(financial_router, "compute_financial_impact", fake_compute)

    response = client.get("/api/s2p/financial-impact")

    assert response.status_code == 200
    assert response.json()["total_decisions"] == 99
    assert calls


def test_financial_impact_has_single_active_route_owner():
    owners = [
        getattr(route.endpoint, "__module__", "")
        for route in app.routes
        if getattr(route, "path", "") == "/api/s2p/financial-impact"
        and "GET" in getattr(route, "methods", set())
    ]

    assert owners == ["app.routers.financial_router"]
