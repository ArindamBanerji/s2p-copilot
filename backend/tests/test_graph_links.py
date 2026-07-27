from __future__ import annotations

from pathlib import Path
import threading

from fastapi.testclient import TestClient

from app.graph_contract import S2P_GRAPH_CONTRACT
from app.main import app, build_s2p_scorer
from app.routers import s2p


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _reset_scorer() -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    from app.graph.s2p_graph_reader import S2PGraphReader

    app.state.s2p_graph_reader = S2PGraphReader(store=app.state.scorer.graph_store)
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
        for path in (BACKEND_ROOT / "app" / "routers").glob("*.py")
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


def test_score_endpoint_creates_invoice_link_immediately():
    _reset_scorer()
    client = TestClient(app)
    invoice_id = "S2P-GS-LINK-SCORE"

    response = client.post("/api/s2p/score", json=_score_payload(invoice_id))

    assert response.status_code == 200
    score = response.json()
    links = app.state.graph_store.get_decision_links(score["decision_id"])
    assert any(
        link["decision_id"] == score["decision_id"]
        and link["entity_id"] == invoice_id
        and link["edge_type"] == "DECIDED_ON"
        for link in links
    )


def test_score_endpoint_still_returns_200_when_linking_fails(monkeypatch):
    _reset_scorer()
    client = TestClient(app)

    def fail_link(decision_id, entity_id, edge_type="DECIDED_ON"):
        raise RuntimeError("link unavailable")

    monkeypatch.setattr(app.state.graph_store, "link_decision_to_entity", fail_link)

    response = client.post("/api/s2p/score", json=_score_payload("S2P-GS-LINK-FAIL"))

    assert response.status_code == 200
    assert response.json()["decision_id"]


def test_outcome_still_returns_200_when_learn_linking_fails(monkeypatch):
    _reset_scorer()
    client = TestClient(app)

    def fail_link(decision_id, entity_id, edge_type="DECIDED_ON"):
        raise RuntimeError("link unavailable")

    monkeypatch.setattr(app.state.graph_store, "link_decision_to_entity", fail_link)

    score_response = client.post("/api/s2p/score", json=_score_payload("S2P-GS-LINK-LEARN-FAIL"))
    assert score_response.status_code == 200
    score = score_response.json()
    assert app.state.graph_store.get_decision_links(score["decision_id"]) == []

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
    payload = outcome_response.json()
    assert payload["decision_id"] == score["decision_id"]
    assert payload["learning_applied"] is True
    assert payload["invoice_id"] == "S2P-GS-LINK-LEARN-FAIL"
    verified = app.state.graph_store.get_verified_decisions(getattr(app.state.graph_store, "domain", "s2p"))
    assert any(row["decision_id"] == score["decision_id"] for row in verified)


class _BlockingGraphStore:
    def __init__(self):
        self.decisions = {
            "decision-a": {"metadata": {"invoice_id": "invoice-a"}, "factors": {}},
            "decision-b": {"metadata": {"invoice_id": "invoice-b"}, "factors": {}},
        }
        self.links = []
        self.first_link_entered = threading.Event()
        self.release_first_link = threading.Event()
        self.second_learn_entered = threading.Event()
        self._lock = threading.Lock()
        self._link_calls = 0
        self.link_decision_to_entity = self._link_decision_to_entity

    def get_decision(self, decision_id: str, domain: str | None = None):
        if domain is not None:
            assert domain == "s2p"
        return self.decisions.get(decision_id)

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict | None = None,
        domain: str | None = None,
    ) -> None:
        raise AssertionError("outcome writes are not part of this double")

    def get_archived_decisions(self, domain: str):
        assert domain == "s2p"
        return []

    def get_decision_links(self, decision_id=None, domain=None, limit=None):
        if domain is not None:
            assert domain == "s2p"
        with self._lock:
            links = list(self.links)
        if decision_id is None:
            filtered = links
        else:
            filtered = [link for link in links if link["decision_id"] == decision_id]
        return filtered[:limit] if limit is not None else filtered

    def _link_decision_to_entity(self, decision_id, entity_id, edge_type="DECIDED_ON"):
        with self._lock:
            self._link_calls += 1
            link_call = self._link_calls
        if link_call == 1:
            self.first_link_entered.set()
            assert self.release_first_link.wait(5)
        with self._lock:
            self.links.append(
                {
                    "decision_id": decision_id,
                    "entity_id": entity_id,
                    "edge_type": edge_type,
                }
            )


class _FakeScorer:
    def __init__(self, graph_store):
        self.graph_store = graph_store

    def learn(self, decision_id, actual_action, outcome, *, context=None):
        if decision_id == "decision-b":
            self.graph_store.second_learn_entered.set()
        invoice_id = (context or {}).get("invoice_id")
        self.graph_store.link_decision_to_entity(decision_id, invoice_id, edge_type="DECIDED_ON")
        return {
            "decision_id": decision_id,
            "status": "recorded",
            "learning_applied": True,
        }


def test_concurrent_learn_restores_original_link_callable():
    graph_store = _BlockingGraphStore()
    scorer = _FakeScorer(graph_store)
    original_link = graph_store.link_decision_to_entity
    results = {}
    errors = []

    def run_learn(decision_id, action):
        try:
            results[decision_id] = s2p._learn_with_scorer(
                scorer,
                decision_id,
                action,
                "confirm",
                {},
            )
        except Exception as exc:  # pragma: no cover - surfaced by assertion below
            errors.append(exc)

    first = threading.Thread(target=run_learn, args=("decision-a", "hold_for_review"))
    first.start()
    assert graph_store.first_link_entered.wait(5)

    second = threading.Thread(target=run_learn, args=("decision-b", "hold_for_review"))
    second.start()
    assert not graph_store.second_learn_entered.wait(1.0)

    graph_store.release_first_link.set()
    first.join(5)
    second.join(5)

    assert errors == []
    assert results["decision-a"]["decision_id"] == "decision-a"
    assert results["decision-b"]["decision_id"] == "decision-b"
    assert graph_store.link_decision_to_entity is original_link
    assert all(
        link["edge_type"] == "DECIDED_ON"
        for link in graph_store.get_decision_links()
    )


def test_non_link_learn_errors_still_propagate():
    class FailingScorer:
        def __init__(self):
            self.graph_store = _BlockingGraphStore()

        def learn(self, decision_id, actual_action, outcome, *, context=None):
            raise ValueError("learn failed")

    scorer = FailingScorer()
    original_link = scorer.graph_store.link_decision_to_entity

    try:
        s2p._learn_with_scorer(scorer, "decision-a", "hold_for_review", "confirm", {})
    except ValueError as exc:
        assert str(exc) == "learn failed"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("non-link learn error was swallowed")
    assert scorer.graph_store.link_decision_to_entity is original_link


def test_s2p_router_links_are_advisory_source_check():
    source = (BACKEND_ROOT / "app" / "routers" / "s2p.py").read_text(encoding="utf-8")

    assert "link_decision_to_entity" in source
    assert "try:" in source
    assert "except Exception:" in source


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
