"""Failure contracts for the ASTRA S2P B enrichment fixes."""

from typing import Any
from pathlib import Path
from unittest.mock import MagicMock
from sqlite3 import OperationalError as SQLiteOperationalError

import pytest
from fastapi.testclient import TestClient
from psycopg import InterfaceError, OperationalError
from ci_platform.graph.age_graph_store import GraphUnavailableError as AGEGraphUnavailableError
from copilot_sdk.graph.memory_store import InMemoryGraphStore

from app.graph.s2p_graph_reader import GraphUnavailableError
from app.main import app
from app.routers import s2p_enrichment, s2p_evidence
from app.services.s2p_enrichment import S2PSupplierEnrichmentService
from app.services.centroid_explorer import S2PCentroidExplorerService, explain_decision, read_dk_weights
from app.services.compounding_ledger import CompoundingLedger, ProposalReadStore
from app.services.proposal_service import GraphProposalStore, ProposalStore
from tests.test_centroid_explorer import FakeScorer, _decision, _seed_store
from tests.test_situation_traversals import ScorerWithWeights, _client, _store


INFRA_FAILURES = (
    ConnectionError, TimeoutError, GraphUnavailableError, AGEGraphUnavailableError,
    OperationalError, InterfaceError, SQLiteOperationalError,
)
PROGRAMMING_ERRORS = (TypeError, ValueError, AttributeError, KeyError)


@pytest.mark.parametrize("error_type", INFRA_FAILURES)
def test_alerts_read_outage(
    memory_store: InMemoryGraphStore, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception],
) -> None:
    monkeypatch.setattr(memory_store, "list_entity_enrichments", MagicMock(side_effect=error_type("offline")))
    monkeypatch.setattr(app.state, "graph_store", memory_store)
    response = TestClient(app).get("/api/s2p/enrichment/alerts")
    assert response.status_code == 200
    assert response.json() == {"alerts": [], "total_alerts": 0, "threshold": 0.1, "alerts_available": False}


def test_alerts_genuinely_empty(memory_store: InMemoryGraphStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "graph_store", memory_store)
    body = TestClient(app).get("/api/s2p/enrichment/alerts").json()
    assert body == {"alerts": [], "total_alerts": 0, "threshold": 0.1, "alerts_available": True}


@pytest.mark.parametrize("result", [None, {}, [None]])
def test_alerts_malformed_graph_result(
    memory_store: InMemoryGraphStore, monkeypatch: pytest.MonkeyPatch, result: Any,
) -> None:
    monkeypatch.setattr(memory_store, "list_entity_enrichments", MagicMock(return_value=result))
    monkeypatch.setattr(app.state, "graph_store", memory_store)
    body = TestClient(app).get("/api/s2p/enrichment/alerts").json()
    assert body["alerts"] == []
    assert body["total_alerts"] == 0
    assert body["alerts_available"] is False


@pytest.mark.parametrize("result", [None, {}, (None, True), ([], None), ([{}], True)])
def test_alerts_malformed_summary(
    memory_store: InMemoryGraphStore, monkeypatch: pytest.MonkeyPatch, result: Any,
) -> None:
    summary = MagicMock(return_value=result)
    monkeypatch.setattr(S2PSupplierEnrichmentService, "summary", summary)
    service = S2PSupplierEnrichmentService(graph_store=memory_store)
    assert service.alerts() is None
    summary.assert_called_once_with(return_status=True)


@pytest.mark.parametrize("error_type", PROGRAMMING_ERRORS)
def test_alerts_programming_errors_propagate(
    memory_store: InMemoryGraphStore, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception],
) -> None:
    monkeypatch.setattr(memory_store, "list_entity_enrichments", MagicMock(side_effect=error_type("bug")))
    monkeypatch.setattr(app.state, "graph_store", memory_store)
    with pytest.raises(error_type):
        TestClient(app).get("/api/s2p/enrichment/alerts")


@pytest.mark.parametrize("factory", [MagicMock(side_effect=ConnectionError("construction")), MagicMock(return_value=None)])
def test_alerts_construction_is_guarded(monkeypatch: pytest.MonkeyPatch, factory: MagicMock) -> None:
    monkeypatch.setattr(s2p_enrichment, "_service", factory)
    body = TestClient(app).get("/api/s2p/enrichment/alerts").json()
    assert body["alerts"] == []
    assert body["alerts_available"] is False


@pytest.mark.parametrize("error_type", INFRA_FAILURES)
def test_compliance_extinction_read_outage(monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]) -> None:
    monkeypatch.setattr(s2p_evidence, "extinction_history", MagicMock(side_effect=error_type("offline")))
    monkeypatch.setattr(s2p_evidence, "_load_invoices", lambda: [])
    monkeypatch.setattr(app.state, "materializer", None, raising=False)
    response = TestClient(app).get("/api/s2p/evidence/compliance")
    assert response.status_code == 200
    body = response.json()
    for key in ("extinction_classes", "extinct_classes", "queue_extinctions"):
        assert body[key] == []
    assert body["extinction_available"] is False
    assert body["extinction_source"] == ""
    assert body["total"] == 0
    assert body["compliant_pct"] == 0.0


@pytest.mark.parametrize("history", [[], [{"category": "price_variance", "earning_decision_id": "D-1"}]])
def test_compliance_extinction_available(monkeypatch: pytest.MonkeyPatch, history: list[dict[str, str]]) -> None:
    monkeypatch.setattr(s2p_evidence, "extinction_history", MagicMock(return_value=history))
    monkeypatch.setattr(s2p_evidence, "_load_invoices", lambda: [])
    monkeypatch.setattr(app.state, "materializer", None, raising=False)
    response = TestClient(app).get("/api/s2p/evidence/compliance")
    assert response.status_code == 200
    body = response.json()
    assert body["extinction_available"] is True
    assert body["extinction_source"] == "persisted_verified_outcome_transitions"
    for key in ("extinction_classes", "extinct_classes", "queue_extinctions"):
        assert body[key] == history


@pytest.mark.parametrize("history", [None, {}, [None]])
def test_compliance_extinction_malformed(monkeypatch: pytest.MonkeyPatch, history: Any) -> None:
    monkeypatch.setattr(s2p_evidence, "extinction_history", MagicMock(return_value=history))
    monkeypatch.setattr(s2p_evidence, "_load_invoices", lambda: [])
    monkeypatch.setattr(app.state, "materializer", None, raising=False)
    body = TestClient(app).get("/api/s2p/evidence/compliance").json()
    assert body["extinction_classes"] == []
    assert body["extinction_available"] is False


@pytest.mark.parametrize("error_type", PROGRAMMING_ERRORS)
def test_compliance_extinction_programming_errors_propagate(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception],
) -> None:
    monkeypatch.setattr(s2p_evidence, "extinction_history", MagicMock(side_effect=error_type("bug")))
    monkeypatch.setattr(s2p_evidence, "_load_invoices", lambda: [])
    monkeypatch.setattr(app.state, "materializer", None, raising=False)
    with pytest.raises(error_type):
        TestClient(app).get("/api/s2p/evidence/compliance")


def test_compliance_extinction_missing_store() -> None:
    body = s2p_evidence.compliance_payload([], None)
    assert body["extinction_classes"] == []
    assert body["extinction_available"] is False


@pytest.mark.parametrize("layer", ["read", "service", "http"])
@pytest.mark.parametrize("state", ["cold", "offline", "ready"])
def test_centroid_dk_read_service_and_serialization(
    monkeypatch: pytest.MonkeyPatch, layer: str, state: str,
) -> None:
    weights = [[0.125] * 8] * 5 if state == "ready" else None
    scorer = FakeScorer(dk_weights=weights)
    if state == "offline":
        monkeypatch.setattr(scorer, "get_dk_weights", MagicMock(side_effect=ConnectionError("offline")))
    available = state != "offline"
    status = {"offline": "unavailable", "cold": "learning", "ready": "available"}[state]
    if layer == "read":
        assert read_dk_weights(scorer) == (weights, available)
        return
    store = _seed_store(_decision())
    if layer == "service":
        explanation = S2PCentroidExplorerService(scorer=scorer, graph_store=store).explain_decision("D-1")
        assert explanation.dk_status == status
        assert explanation.dk_available is available
        if state != "ready":
            assert all(row.dk_weight is None for row in explanation.factor_contributions)
        body = explanation.to_dict()
    else:
        monkeypatch.setattr(app.state, "scorer", scorer)
        monkeypatch.setattr(app.state, "graph_store", store)
        response = TestClient(app).get("/api/s2p/centroid/explain/D-1")
        assert response.status_code == 200
        body = response.json()
    assert body["dk_status"] == status
    assert body["dk_available"] is available
    assert body["recommended_action"] == "auto_approve"
    assert body["factor_contributions"]
    for row in body["factor_contributions"]:
        assert row["dk_status"] == status
        assert isinstance(row["dk_weight"], float)
        assert row["dk_weight"] == (0.125 if state == "ready" else 0.0)


@pytest.mark.parametrize("error_type", INFRA_FAILURES)
def test_read_dk_weights_infrastructure_errors(monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]) -> None:
    scorer = FakeScorer()
    monkeypatch.setattr(scorer, "get_dk_weights", MagicMock(side_effect=error_type("offline")))
    assert read_dk_weights(scorer) == (None, False)


@pytest.mark.parametrize("weights", [{}, [], "bad", [1.0], [float("nan")] * 8, [[None] * 8, [0.125] * 8]])
def test_centroid_dk_malformed_read_and_explanation(weights: Any) -> None:
    scorer = FakeScorer(dk_weights=weights)
    assert read_dk_weights(scorer) == (None, False)
    explanation = explain_decision(_decision(), scorer, dk_weights=weights)
    assert explanation.dk_available is False
    assert explanation.dk_status == "unavailable"
    assert all(row["dk_weight"] == 0.0 for row in explanation.to_dict()["factor_contributions"])


@pytest.mark.parametrize("error_type", PROGRAMMING_ERRORS)
def test_centroid_dk_programming_errors_propagate(monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]) -> None:
    scorer = FakeScorer()
    monkeypatch.setattr(scorer, "get_dk_weights", MagicMock(side_effect=error_type("bug")))
    monkeypatch.setattr(app.state, "scorer", scorer)
    monkeypatch.setattr(app.state, "graph_store", _seed_store(_decision()))
    with pytest.raises(error_type):
        TestClient(app).get("/api/s2p/centroid/explain/D-1")


def test_read_dk_weights_accessor_construction_guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeScorer, "get_dk_weights", property(MagicMock(side_effect=ConnectionError("accessor"))))
    assert read_dk_weights(FakeScorer()) == (None, False)


@pytest.mark.parametrize("missing_scorer", [True, False])
def test_read_dk_weights_missing_reader(monkeypatch: pytest.MonkeyPatch, missing_scorer: bool) -> None:
    scorer = None if missing_scorer else FakeScorer()
    if scorer is not None:
        monkeypatch.setattr(scorer, "get_dk_weights", None)
    assert read_dk_weights(scorer) == (None, False)


@pytest.mark.parametrize("state", ["cold", "offline", "ready", "malformed"])
def test_situation_dk_weights_availability(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    store, decision_id = _store("price_variance")
    weights: dict[str, Any] = {"cold": None, "ready": [[0.125] * 8], "malformed": {}}
    read = MagicMock(side_effect=ConnectionError("offline")) if state == "offline" else MagicMock(return_value=weights[state])
    monkeypatch.setattr(ScorerWithWeights, "get_dk_weights", read)
    response = _client(store).get(f"/api/s2p/situation/{decision_id}")
    assert response.status_code == 200
    body = response.json()
    expected_status = {"cold": "learning", "ready": "available", "offline": "unavailable", "malformed": "unavailable"}
    assert body["dk_status"] == expected_status[state]
    assert body["dk_available"] is (state in {"cold", "ready"})
    assert isinstance(body["nl_explanation"], str)
    assert isinstance(body["context_chain"], list)
    read.assert_called_once()


@pytest.mark.parametrize("error_type", PROGRAMMING_ERRORS)
def test_situation_dk_programming_errors_propagate(monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]) -> None:
    store, decision_id = _store("price_variance")
    monkeypatch.setattr(ScorerWithWeights, "get_dk_weights", MagicMock(side_effect=error_type("bug")))
    with pytest.raises(error_type):
        _client(store).get(f"/api/s2p/situation/{decision_id}")


@pytest.mark.parametrize("backend", ["graph", "sqlite"])
def test_proposal_store_read_interface(
    memory_store: InMemoryGraphStore, tmp_path: Path, backend: str,
) -> None:
    store = GraphProposalStore(memory_store) if backend == "graph" else ProposalStore(str(tmp_path / "proposals.sqlite"))
    reader: ProposalReadStore = store
    try:
        assert reader.list_recent() == []
        assert reader.get_outcome("absent") is None
        assert reader.count() == 0
        ledger = CompoundingLedger(proposal_store=reader, graph_store=memory_store)
        assert ledger.proposal_store is store
        assert ledger.timeline() == []
    finally:
        store.close()
