from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "app" / "routers"
client = TestClient(app)


def _reset_scorer() -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def _write_decision(invoice_id: str, metadata_invoice_id: str | None = None) -> str:
    graph_store = app.state.graph_store
    metadata = {"invoice_id": metadata_invoice_id} if metadata_invoice_id is not None else {}
    metadata.setdefault("entity_id", f"entity-{invoice_id}")
    return graph_store.write_decision(
        getattr(graph_store, "domain", "s2p"),
        category="price_variance",
        action="approve",
        confidence=0.91,
        factors={"match_status": 0.9},
        metadata=metadata,
    )


def test_evidence_uses_graph_links() -> None:
    _reset_scorer()
    invoice_id = "S2P-EVIDENCE-GRAPH-001"
    decision_id = _write_decision(invoice_id, metadata_invoice_id="S2P-EVIDENCE-OTHER")
    app.state.graph_store.link_decision_to_entity(
        decision_id,
        invoice_id,
        edge_type="DECIDED_ON",
    )

    response = client.get(f"/api/s2p/evidence/audit-trail/{invoice_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == invoice_id
    assert data["count"] == 1
    assert data["decisions"][0]["decision_id"] == decision_id
    assert data["decisions"][0]["metadata"]["invoice_id"] == "S2P-EVIDENCE-OTHER"


def test_evidence_fallback_to_metadata() -> None:
    _reset_scorer()
    invoice_id = "S2P-EVIDENCE-METADATA-001"
    decision_id = _write_decision(invoice_id, metadata_invoice_id=invoice_id)

    response = client.get(f"/api/s2p/evidence/audit-trail/{invoice_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["decisions"][0]["decision_id"] == decision_id
    assert data["decisions"][0]["invoice_id"] == invoice_id


def test_evidence_response_shape_unchanged() -> None:
    _reset_scorer()
    graph_invoice_id = "S2P-EVIDENCE-SHAPE-GRAPH"
    metadata_invoice_id = "S2P-EVIDENCE-SHAPE-METADATA"
    graph_decision_id = _write_decision(graph_invoice_id, metadata_invoice_id="OTHER")
    metadata_decision_id = _write_decision(metadata_invoice_id, metadata_invoice_id=metadata_invoice_id)
    app.state.graph_store.link_decision_to_entity(
        graph_decision_id,
        graph_invoice_id,
        edge_type="DECIDED_ON",
    )

    graph_response = client.get(f"/api/s2p/evidence/audit-trail/{graph_invoice_id}")
    metadata_response = client.get(f"/api/s2p/evidence/audit-trail/{metadata_invoice_id}")

    assert graph_response.status_code == 200
    assert metadata_response.status_code == 200
    graph_data = graph_response.json()
    metadata_data = metadata_response.json()
    assert set(graph_data) == {"invoice_id", "decisions", "count"}
    assert set(graph_data) == set(metadata_data)
    assert isinstance(graph_data["invoice_id"], str)
    assert isinstance(graph_data["decisions"], list)
    assert isinstance(graph_data["count"], int)
    assert metadata_data["decisions"][0]["decision_id"] == metadata_decision_id


def test_evidence_uses_canonical_app_state_graph_store() -> None:
    source = (ROUTERS / "s2p_evidence.py").read_text(encoding="utf-8")

    assert "request.app.state.graph_store" in source
    assert "get_decision_links" in source
    assert "InMemoryGraphStore(" not in source


def test_no_new_graphstore_subclass_or_adapter() -> None:
    source = "\n".join(
        [
            (ROOT / "app" / "main.py").read_text(encoding="utf-8"),
            (ROUTERS / "s2p_evidence.py").read_text(encoding="utf-8"),
        ]
    )

    assert re.search(r"class\s+\w*GraphStore\b", source) is None
    assert re.search(r"class\s+\w*Store\s*[:(]", source) is None


def test_no_new_duplicate_find_invoice_helpers() -> None:
    definitions = []
    for path in ROUTERS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"def\s+_?find_invoice\s*\(", source):
            definitions.append(path.name)

    assert definitions == ["s2p_data_helpers.py"]
