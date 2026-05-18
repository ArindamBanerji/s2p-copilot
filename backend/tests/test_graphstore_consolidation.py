import ast
import json
import os
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p_data_helpers  # noqa: E402
from app.routers.s2p_data_helpers import find_invoice  # noqa: E402
from copilot_sdk.graph.memory_store import InMemoryGraphStore  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "app" / "routers"


def _reset_s2p_state() -> None:
    scorer = build_s2p_scorer()
    app.state.scorer = scorer
    app.state.graph_store = scorer.graph_store
    app.state.s2p_reward_function = scorer._reward_fn


def _score_payload(event_id: str, invoice_id: str) -> dict[str, object]:
    return {
        "event_id": event_id,
        "category": "price_variance",
        "amount": 2500.0,
        "supplier_id": "SUP-001",
        "contract_id": "CON-001",
        "approved_categories": ["price_variance"],
        "historical_spend_mean": 2400.0,
        "historical_spend_std": 100.0,
        "commodity_index_correlation": 0.8,
        "invoice_id": invoice_id,
    }


def test_no_private_graphstore_in_s2p_main() -> None:
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    graphstore_classes = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith("GraphStore")
    ]

    assert "_S2PGraphStore" not in source
    assert graphstore_classes == []
    assert 'InMemoryGraphStore(decision_id_prefix="S2P-")' in source


def test_s2p_uses_canonical_inmemory_graphstore_with_prefix() -> None:
    scorer = build_s2p_scorer()
    graph_store = scorer.graph_store

    assert isinstance(graph_store, InMemoryGraphStore)
    decision_id = graph_store.write_decision(
        entity_id="invoice-1",
        category="price_variance",
        action="approve",
        confidence=0.91,
        factors={"match_status": 1.0},
        metadata={"decision_id": "abc123"},
    )

    stored = graph_store.get_decision(decision_id)
    assert decision_id.startswith("S2P-")
    assert stored is not None
    assert stored["decision_id"] == decision_id
    assert stored["metadata"]["decision_id"] == decision_id


def test_score_endpoint_preserves_s2p_decision_prefix() -> None:
    _reset_s2p_state()

    with TestClient(app) as client:
        response = client.post(
            "/api/s2p/score",
            json=_score_payload("S2P-EVT-GRAPHSTORE", "S2P-INV-GRAPHSTORE"),
        )

    assert response.status_code == 200
    decision_id = response.json()["decision_id"]
    stored = app.state.graph_store.get_decision(decision_id)
    assert decision_id.startswith("S2P-")
    assert stored["metadata"]["decision_id"] == decision_id


def test_find_invoice_defined_once() -> None:
    definitions = []
    for path in ROUTERS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"^def _?find_invoice\(", source, flags=re.MULTILINE):
            definitions.append(path.name)

    assert definitions == ["s2p_data_helpers.py"]


def test_load_json_defined_once_or_consolidated() -> None:
    helper_source = (ROUTERS / "s2p_data_helpers.py").read_text(encoding="utf-8")
    duplicate_private_defs = [
        path.name
        for path in ROUTERS.glob("*.py")
        if re.search(r"^def _load_json\(", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    ]

    assert "def load_json(" in helper_source
    assert duplicate_private_defs == []


def test_find_invoice_by_invoice_id(monkeypatch, tmp_path) -> None:
    invoices = [
        {
            "event_id": "EVENT-100",
            "invoice_id": "INV-100",
            "supplier_id": "SUP-100",
            "amount": 1234,
        }
    ]
    (tmp_path / "synthetic_invoices.json").write_text(json.dumps(invoices), encoding="utf-8")
    monkeypatch.setattr(s2p_data_helpers, "_DATA_DIR", tmp_path)

    found = find_invoice("INV-100")

    assert found is not None
    assert found["invoice_id"] == "INV-100"


def test_find_invoice_by_event_id(monkeypatch, tmp_path) -> None:
    invoices = [
        {
            "event_id": "EVENT-200",
            "invoice_id": "INV-200",
            "supplier_id": "SUP-200",
            "amount": 5678,
        }
    ]
    (tmp_path / "synthetic_invoices.json").write_text(json.dumps(invoices), encoding="utf-8")
    monkeypatch.setattr(s2p_data_helpers, "_DATA_DIR", tmp_path)

    found = find_invoice("EVENT-200")

    assert found is not None
    assert found["invoice_id"] == "INV-200"


def test_affected_s2p_endpoints_still_work() -> None:
    _reset_s2p_state()

    with TestClient(app) as client:
        score = client.post(
            "/api/s2p/score",
            json=_score_payload("S2P-EVT-ENDPOINTS", "S2P-INV-ENDPOINTS"),
        )
        control_tower = client.get("/api/s2p/control-tower/classify", params={"invoice_id": "S2P-INV-0001"})
        insight = client.get("/api/s2p/insight/fingerprint", params={"invoice_id": "S2P-INV-0001"})
        pvg = client.get("/api/s2p/pvg/variants")

    assert score.status_code == 200
    assert score.json()["decision_id"].startswith("S2P-")
    assert control_tower.status_code == 200
    assert insight.status_code == 200
    assert pvg.status_code == 200
