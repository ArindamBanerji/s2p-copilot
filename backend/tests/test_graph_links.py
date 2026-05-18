from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.graph_contract import S2P_GRAPH_CONTRACT
from app.main import app, build_s2p_scorer


def _reset_scorer() -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def _score_payload(invoice_id: str = "S2P-GS-LINK-001") -> dict:
    return {
        "event_id": invoice_id,
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-GS",
        "match_status": 0.95,
        "amount_variance_ratio": 0.15,
        "duplicate_score": 0.02,
        "supplier_exception_history": 0.03,
        "payment_terms_impact": 0.50,
        "commodity_index_correlation": 0.80,
        "tax_regulatory_compliance": 0.95,
    }


def _score_then_outcome(client: TestClient, invoice_id: str = "S2P-GS-LINK-001") -> dict:
    score_response = client.post("/api/s2p/score", json=_score_payload(invoice_id))
    assert score_response.status_code == 200
    score = score_response.json()

    outcome_response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "analyst-gs-link",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
            "amount": 5000.0,
            "at_risk": 1000.0,
            "recovery_pct": 80.0,
        },
    )
    assert outcome_response.status_code == 200
    return score


def test_no_invoice_map_in_source():
    forbidden = (
        "app.state.s2p_decision_invoice_index",
        "s2p_decision_invoice_index",
        "decision_invoice_index",
        "_invoice_map",
    )
    router_sources = "".join(
        path.read_text(encoding="utf-8")
        for path in Path("app/routers").glob("*.py")
    )

    for token in forbidden:
        assert token not in router_sources


def test_score_then_learn_links_invoice():
    _reset_scorer()
    client = TestClient(app)
    invoice_id = "S2P-GS-LINK-001"

    score = _score_then_outcome(client, invoice_id)

    links = app.state.scorer.graph_store.get_decision_links(score["decision_id"])
    assert links == [
        {
            "decision_id": score["decision_id"],
            "entity_id": invoice_id,
            "edge_type": "DECIDED_ON",
            "created_at": links[0]["created_at"],
        }
    ]


def test_linked_invoice_retrievable():
    _reset_scorer()
    client = TestClient(app)
    invoice_id = "S2P-GS-LINK-002"

    score = _score_then_outcome(client, invoice_id)

    all_links = app.state.graph_store.get_decision_links()
    assert any(
        link["decision_id"] == score["decision_id"]
        and link["entity_id"] == invoice_id
        and link["edge_type"] == "DECIDED_ON"
        for link in all_links
    )


def test_graph_contract_includes_decision_edge():
    assert any(
        edge.label == "DECIDED_ON"
        and edge.from_label == "Decision"
        and edge.to_label == "Invoice"
        for edge in S2P_GRAPH_CONTRACT.edge_types
    )
    assert S2P_GRAPH_CONTRACT.validate() == []
