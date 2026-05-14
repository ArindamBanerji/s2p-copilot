import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app, build_s2p_scorer


client = TestClient(app)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    if hasattr(app.state, "s2p_decision_invoice_index"):
        delattr(app.state, "s2p_decision_invoice_index")
    return app.state.scorer


def _first_invoice() -> dict:
    invoices = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))
    return invoices[0]


def _score_invoice() -> tuple[dict, dict]:
    invoice = _first_invoice()
    payload = {
        "event_id": invoice["invoice_id"],
        "category": invoice["category"],
        "amount": invoice["amount"],
        "supplier_id": invoice["supplier_id"],
    }
    response = client.post("/api/s2p/score", json=payload)
    assert response.status_code == 200
    return invoice, response.json()


def test_s2p_scorer_uses_sdk_preset():
    scorer = reset_sdk_scorer()

    assert scorer._preset.name == "s2p"
    assert scorer._preset.shape.n_categories == 5
    assert scorer._preset.shape.n_actions == 5
    assert scorer._preset.shape.n_factors == 7


def test_score_response_shape_preserved_and_invoice_metadata_persisted():
    reset_sdk_scorer()
    invoice, scored = _score_invoice()

    for key in ("decision_id", "action", "action_index", "confidence", "factor_vector", "factor_names"):
        assert key in scored
    decision = app.state.graph_store.get_decision(scored["decision_id"])
    assert decision is not None
    assert decision["metadata"]["invoice_id"] == invoice["invoice_id"]
    assert decision["metadata"]["source_invoice_id"] == invoice["invoice_id"]
    assert not hasattr(app.state, "s2p_decision_invoice_index")


def test_outcome_uses_persistent_invoice_link_without_memory_index():
    reset_sdk_scorer()
    invoice, scored = _score_invoice()
    if hasattr(app.state, "s2p_decision_invoice_index"):
        delattr(app.state, "s2p_decision_invoice_index")

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": invoice["category"],
            "predicted_action": scored["action"],
            "recovery_pct": 80,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["learning_applied"] is True
    assert data["reward_raw"] == 0.8
    assert data["invoice_id"] == invoice["invoice_id"]
    decision = app.state.graph_store.get_decision(scored["decision_id"])
    assert decision["metadata"]["invoice_id"] == invoice["invoice_id"]


def test_evidence_uses_persistent_invoice_link_without_memory_index():
    reset_sdk_scorer()
    invoice, scored = _score_invoice()
    if hasattr(app.state, "s2p_decision_invoice_index"):
        delattr(app.state, "s2p_decision_invoice_index")

    response = client.get(f"/api/s2p/evidence/audit-trail/{invoice['invoice_id']}")

    assert response.status_code == 200
    data = response.json()
    matched = [
        decision
        for decision in data["decisions"]
        if decision.get("decision_id") == scored["decision_id"]
    ]
    assert matched
    assert matched[0]["metadata"]["invoice_id"] == invoice["invoice_id"]
    assert matched[0]["invoice_id"] == invoice["invoice_id"]


def test_evidence_accepts_decision_id_and_enriches_invoice_metadata():
    reset_sdk_scorer()
    invoice, scored = _score_invoice()

    response = client.get(f"/api/s2p/evidence/audit-trail/{scored['decision_id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["decisions"][0]["metadata"]["invoice_id"] == invoice["invoice_id"]
    assert data["decisions"][0]["invoice_id"] == invoice["invoice_id"]


def test_bridge_and_memory_index_removed_from_app_sources():
    source_paths = [
        Path("app/routers/s2p.py"),
        Path("app/routers/s2p_evidence.py"),
    ]
    forbidden = (
        "s2p_decision_invoice_index",
        "_decision_invoice_index",
        "_index_score_decision",
        "_ContextualRewardFunction",
        "threading.Lock",
    )

    for path in source_paths:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text
