from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = BACKEND_ROOT / "app" / "routers" / "s2p.py"


@pytest.fixture()
def client():
    scorer = build_s2p_scorer()
    app.state.scorer = scorer
    app.state.graph_store = scorer.graph_store
    app.state.s2p_reward_function = scorer._reward_fn
    return TestClient(app)


def _score_payload() -> dict:
    return {
        "event_id": "CTX-BRIDGE-001",
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-CTX",
        "match_status": 0.95,
        "amount_variance_ratio": 0.15,
        "duplicate_score": 0.02,
        "supplier_exception_history": 0.03,
        "payment_terms_impact": 0.50,
        "commodity_index_correlation": 0.80,
        "tax_regulatory_compliance": 0.95,
    }


def test_bridge_removed():
    source = ROUTER_PATH.read_text(encoding="utf-8")

    assert "_ContextualRewardFunction" not in source
    assert "set_context" not in source
    assert "clear_context" not in source


def test_no_threading_lock_in_router():
    source = ROUTER_PATH.read_text(encoding="utf-8")

    assert "threading.Lock" not in source
    assert "context_bridge" not in source


def test_learn_with_invoice_context(client):
    score_response = client.post("/api/s2p/score", json=_score_payload())
    assert score_response.status_code == 200
    score = score_response.json()
    assert score["action"] in S2PDomainConfig.actions

    outcome_response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "analyst-1",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
            "amount": 5000.0,
            "at_risk": 1000.0,
            "recovery_pct": 80.0,
        },
    )

    assert outcome_response.status_code == 200
    payload = outcome_response.json()
    assert payload["decision_id"] == score["decision_id"]
    assert payload["reward_raw"] == pytest.approx(0.8)
    assert payload["reward"] == pytest.approx(0.8)

    graph_store = app.state.scorer.graph_store
    verified = graph_store.get_verified_decisions(getattr(graph_store, "domain", "s2p"))
    matching = [row for row in verified if row["decision_id"] == score["decision_id"]]
    assert len(matching) == 1
    context = matching[0]["outcome_metadata"]["context"]
    assert context["invoice_id"] == "CTX-BRIDGE-001"
    assert context["amount"] == 5000.0
    assert context["total_amount"] == 5000.0
    assert context["supplier_id"] == "SUP-CTX"
    assert context["supplier"] == "SUP-CTX"
    assert context["amount_variance_ratio"] == 0.15
    assert context["recovery_pct"] == 80.0
