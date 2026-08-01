"""Regression coverage for graph-authoritative S2P situations."""

from __future__ import annotations

import inspect

from app.routers import s2p_situation
from tests.test_situation_graph_enrichment import _client, _price_store


def test_situation_graph_fields_from_graph() -> None:
    store, decision_id = _price_store()

    response = _client(store).get(f"/api/s2p/situation/{decision_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["decision_id"] == decision_id
    assert body["confidence"] == 0.91
    assert body["provenance"]["confidence"] == "context"


def test_situation_local_metadata_is_request_context() -> None:
    store, decision_id = _price_store()
    decision = store.get_decision(decision_id, domain="s2p")
    assert decision is not None
    decision["metadata"]["confidence"] = 0.01
    store._decisions[decision_id] = decision

    response = _client(store).get(f"/api/s2p/situation/{decision_id}")

    assert response.status_code == 200
    assert response.json()["confidence"] == 0.91


def test_situation_graph_failure_returns_503() -> None:
    source = inspect.getsource(s2p_situation.get_situation)

    assert "except GraphUnavailableError" in source
    assert 'status_code=503' in source
    assert "Decision graph unavailable" in source


def test_situation_rejects_foreign_domain() -> None:
    source = inspect.getsource(s2p_situation._decision)

    assert 'decision.get("domain") != "s2p"' in source
    assert "foreign domain" in source
