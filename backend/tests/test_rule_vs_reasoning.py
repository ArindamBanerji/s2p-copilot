from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, build_s2p_scorer
from app.routers.s2p import compute_threshold_decision


def _client() -> TestClient:
    scorer = build_s2p_scorer()
    app.state.scorer = scorer
    app.state.graph_store = scorer.graph_store
    app.state.s2p_reward_function = scorer._reward_fn
    return TestClient(app)


def _score_payload(amount_variance_ratio: float = 0.052) -> dict:
    return {
        "event_id": "RULE-CONTRAST-001",
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-RULE",
        "match_status": 0.95,
        "amount_variance_ratio": amount_variance_ratio,
        "duplicate_score": 0.02,
        "supplier_exception_history": 0.03,
        "payment_terms_impact": 0.50,
        "commodity_index_correlation": 0.80,
        "tax_regulatory_compliance": 0.95,
    }


def test_threshold_decision_rejects_above_5pct() -> None:
    result = compute_threshold_decision({"amount_variance_ratio": 0.052})

    assert result["decision"] == "REJECT"
    assert "exceeds 5.0% threshold" in result["reason"]
    assert result["price_variance_pct"] == 5.2


def test_threshold_decision_approves_below_5pct() -> None:
    result = compute_threshold_decision({"amount_variance_ratio": 0.041})

    assert result["decision"] == "APPROVE"
    assert result["reason"] == "Within threshold"
    assert result["price_variance_pct"] == 4.1


def test_contrast_endpoint_returns_both_decisions() -> None:
    response = _client().post("/api/s2p/score", json=_score_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["threshold_decision"]["decision"] == "REJECT"
    assert payload["threshold_decision"]["price_variance_pct"] == 5.2
    assert payload["action"] in {
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    }
