"""
tests/test_s2p_preview.py - S2P v2 preview endpoint tests.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app

client = TestClient(app)


def _queue(limit: int | None = None):
    path = "/api/s2p/preview/queue"
    if limit is not None:
        path = f"{path}?limit={limit}"
    return client.get(path)


def test_queue_returns_200():
    assert _queue().status_code == 200


def test_queue_default_limit_5():
    data = _queue().json()
    assert data["total"] == 50
    assert data["showing"] == 5
    assert len(data["invoices"]) == 5


def test_queue_custom_limit():
    data = _queue(12).json()
    assert data["showing"] == 12
    assert len(data["invoices"]) == 12


def test_queue_invoices_have_required_fields():
    invoice = _queue().json()["invoices"][0]
    for key in (
        "invoice_id",
        "supplier_id",
        "supplier_name",
        "category",
        "amount",
        "po_reference",
        "variance_pct",
        "recommended_action",
        "confidence",
        "probabilities",
        "factors",
        "factor_vector",
        "ground_truth_action",
    ):
        assert key in invoice


def test_queue_factor_vector_length_7():
    invoices = _queue(50).json()["invoices"]
    assert all(len(invoice["factor_vector"]) == 7 for invoice in invoices)
    assert all(len(invoice["factors"]) == 7 for invoice in invoices)


def test_queue_scorer_metadata():
    scorer = _queue().json()["scorer"]
    assert scorer["engine"] == "Graph Attention Engine"
    assert scorer["tensor_shape"] == "(5, 5, 7)"
    assert scorer["factors"] == S2PDomainConfig.factors
    assert "version" in scorer


def test_conservation_returns_200():
    assert client.get("/api/s2p/preview/conservation").status_code == 200


def test_conservation_has_status():
    data = client.get("/api/s2p/preview/conservation").json()
    assert data["status"] in ("GREEN", "AMBER", "RED")


def test_conservation_has_auto_approve_pct():
    data = client.get("/api/s2p/preview/conservation").json()
    assert 0.0 <= data["auto_approve_pct"] <= 100.0
    assert data["verified_decisions"] == 1000
    assert data["fixture_decisions"] == 50


def test_conservation_has_engine_version():
    data = client.get("/api/s2p/preview/conservation").json()
    assert "engine_version" in data


def test_compounding_returns_200():
    assert client.get("/api/s2p/preview/compounding").status_code == 200


def test_compounding_trajectory_has_20_points():
    data = client.get("/api/s2p/preview/compounding").json()
    assert len(data["trajectory"]) == 20


def test_compounding_accuracy_increases():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    assert trajectory[-1]["accuracy"] > trajectory[0]["accuracy"] + 0.01


def test_compounding_initial_accuracy():
    data = client.get("/api/s2p/preview/compounding").json()
    assert data["initial_accuracy"] == data["trajectory"][0]["accuracy"]
    assert data["source"] == "s2p_preview_simulation"
    assert data["source"] != "synthetic_demo"


def test_compounding_uses_s2p_tensor_shape():
    data = client.get("/api/s2p/preview/compounding").json()
    assert data["tensor_shape"] == [5, 5, 7]


def test_compounding_accuracy_values_in_range():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    assert all(0.0 <= point["accuracy"] <= 1.0 for point in trajectory)


def test_compounding_points_ordered_by_decision_number():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    decision_numbers = [point["decision_number"] for point in trajectory]
    assert decision_numbers == sorted(decision_numbers)
    assert all(point["decisions"] == point["decision_number"] for point in trajectory)


def test_compounding_last_segment_improves_over_first_segment():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    segment_size = max(1, len(trajectory) // 5)
    first_avg = sum(point["accuracy"] for point in trajectory[:segment_size]) / segment_size
    last_avg = sum(point["accuracy"] for point in trajectory[-segment_size:]) / segment_size
    assert last_avg > first_avg + 0.01


def test_suppliers_returns_200():
    assert client.get("/api/s2p/preview/suppliers").status_code == 200


def test_suppliers_default_returns_all():
    data = client.get("/api/s2p/preview/suppliers").json()
    assert data["total"] == 10
    assert data["showing"] == 10
    assert len(data["suppliers"]) == 10


def test_suppliers_explicit_limit_2():
    data = client.get("/api/s2p/preview/suppliers?limit=2").json()
    assert data["total"] == 10
    assert data["showing"] == 2
    assert len(data["suppliers"]) == 2


def test_suppliers_have_required_fields():
    supplier = client.get("/api/s2p/preview/suppliers").json()["suppliers"][0]
    for key in (
        "supplier_id",
        "supplier_name",
        "region",
        "otif",
        "exception_rate",
        "lead_time",
        "financial_health_trend",
    ):
        assert key in supplier


def test_suppliers_chen_lin_present():
    suppliers = client.get("/api/s2p/preview/suppliers?limit=10").json()["suppliers"]
    assert any(supplier["supplier_name"] == "Chen-Lin Mfg" for supplier in suppliers)


def test_config_returns_200():
    assert client.get("/api/s2p/preview/config").status_code == 200


def test_config_tensor_shape():
    data = client.get("/api/s2p/preview/config").json()
    assert data["tensor_shape"] == "(5, 5, 7)"


def test_config_factors_count_7():
    data = client.get("/api/s2p/preview/config").json()
    assert len(data["factors"]) == 7
    assert data["factors"] == S2PDomainConfig.factors


def test_queue_actions_are_v2():
    invoices = _queue(50).json()["invoices"]
    v2_actions = set(S2PDomainConfig.actions)
    legacy_actions = {"approve", "escalate", "reject", "review"}
    assert all(invoice["recommended_action"] in v2_actions for invoice in invoices)
    assert all(invoice["recommended_action"] not in legacy_actions for invoice in invoices)


def test_queue_categories_are_v2():
    invoices = _queue(50).json()["invoices"]
    v2_categories = set(S2PDomainConfig.categories)
    legacy_categories = {
        "maverick_spend",
        "supplier_risk",
        "contract_breach",
        "budget_overrun",
        "approval_bypass",
        "data_quality",
    }
    assert all(invoice["category"] in v2_categories for invoice in invoices)
    assert all(invoice["category"] not in legacy_categories for invoice in invoices)


def test_score_endpoint_is_canonical_too():
    canonical_payload = {
        "event_id": "E001",
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-001",
        "match_status": 0.9,
        "amount_variance_ratio": 0.08,
        "duplicate_score": 0.04,
        "supplier_exception_history": 0.05,
        "payment_terms_impact": 0.48,
        "commodity_index_correlation": 0.76,
        "tax_regulatory_compliance": 0.90,
    }
    score = client.post("/api/s2p/score", json=canonical_payload)
    preview = client.get("/api/s2p/preview/queue")

    assert score.status_code == 200
    assert preview.status_code == 200
    assert len(score.json()["factor_vector"]) == 7
    assert len(preview.json()["invoices"][0]["factor_vector"]) == 7


def test_reset_clears_cache():
    import app.routers.s2p_preview as preview_module

    assert client.get("/api/s2p/preview/queue").status_code == 200
    assert preview_module._scored_invoices is not None

    preview_module.reset_preview_state()
    assert preview_module._invoices is None
    assert preview_module._scored_invoices is None

    response = client.get("/api/s2p/preview/queue")
    assert response.status_code == 200
    assert response.json()["total"] == 50


def test_preview_module_has_no_profile_scorer_reference():
    import pathlib

    source = pathlib.Path("app/routers/s2p_preview.py").read_text(encoding="utf-8")
    assert "ProfileScorer" not in source


def test_preview_queue_uses_app_state_scorer(monkeypatch):
    import app.routers.s2p_preview as preview_module
    from types import SimpleNamespace

    class SentinelScorer:
        def score(self, factors, category, metadata=None):
            return SimpleNamespace(
                action="auto_approve",
                action_index=0,
                confidence=0.99,
                probabilities=[1.0, 0.0, 0.0, 0.0, 0.0],
                decision_id="SENTINEL",
            )

    preview_module.reset_preview_state()
    monkeypatch.setattr(app.state, "scorer", SentinelScorer(), raising=False)
    response = client.get("/api/s2p/preview/queue?limit=1")
    data = response.json()

    assert response.status_code == 200
    assert data["invoices"][0]["recommended_action"] == "auto_approve"
    assert data["invoices"][0]["confidence"] == 0.99
