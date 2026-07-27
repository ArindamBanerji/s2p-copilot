from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.graph.s2p_graph_reader import S2PGraphReader
from app.main import app, build_s2p_scorer


client = TestClient(app)


class ReadOnlyGraphStore:
    domain = "s2p"

    def __init__(self, decisions: list[dict] | None = None):
        self.decisions = decisions or []
        self.write_calls = 0

    def get_all_decisions(self, domain: str | None = None) -> list[dict]:
        if domain is not None:
            assert domain == "s2p"
        return list(self.decisions)

    def get_decision(self, decision_id: str, domain: str | None = None):
        if domain is not None:
            assert domain == self.domain
        return next((row for row in self.decisions if row.get("decision_id") == decision_id), None)

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict | None = None,
        domain: str | None = None,
    ) -> None:
        self.write_calls += 1
        raise AssertionError("audit export must not write outcomes")

    def get_archived_decisions(self, domain: str) -> list[dict]:
        assert domain == self.domain
        return []

    def count_verified(self, domain: str = "s2p") -> int:
        assert domain == "s2p"
        return sum(1 for decision in self.decisions if decision.get("is_correct") is not None)

    def count_correct(self, domain: str = "s2p") -> int:
        assert domain == "s2p"
        return sum(1 for decision in self.decisions if decision.get("is_correct") is True)

    def add_decision(self, decision: dict | None = None) -> None:
        self.write_calls += 1
        raise AssertionError("audit export must not write decisions")

    def link_decision_to_entity(
        self,
        decision_id: str,
        entity_id: str,
        edge_type: str = "DECIDED_ON",
    ) -> None:
        self.write_calls += 1
        raise AssertionError("audit export must not write graph links")


def reset_sdk_scorer() -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_graph_reader = S2PGraphReader(store=app.state.scorer.graph_store)
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def test_audit_export_returns_required_sections() -> None:
    reset_sdk_scorer()

    response = client.get("/api/s2p/audit/export")

    assert response.status_code == 200
    payload = response.json()
    assert {
        "summary",
        "decision_chain",
        "conservation_proof",
        "factor_analysis",
        "financial_impact",
        "sox_compliance",
        "source",
        "engine",
    } <= set(payload)
    assert payload["source"] == "live"


def test_audit_export_summary_and_conservation_fields() -> None:
    reset_sdk_scorer()

    payload = client.get("/api/s2p/audit/export").json()

    assert {
        "export_timestamp",
        "export_version",
        "total_decisions",
        "verified_decisions",
        "correct_decisions",
        "accuracy",
        "conservation_status",
        "iks",
        "date_range",
    } <= set(payload["summary"])
    conservation = payload["conservation_proof"]
    assert {"theta_min", "verified", "penalty_ratio", "q", "status"} <= set(conservation)
    assert conservation["penalty_ratio"] == 5.0


def test_audit_export_sox_and_financial_sections() -> None:
    reset_sdk_scorer()

    payload = client.get("/api/s2p/audit/export").json()

    sox = payload["sox_compliance"]
    assert "tamper_evident" in sox
    assert "recommendation" in sox
    assert sox["recommendation"] in {"SOX-ready", "insufficient_evidence"}
    financial = payload["financial_impact"]
    assert "total_invoices_processed" in financial
    assert "total_amount" in financial
    assert financial["total_amount"] >= 0


def test_audit_export_csv_returns_json_tabular_rows() -> None:
    reset_sdk_scorer()

    response = client.get("/api/s2p/audit/export/csv")

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "json_tabular"
    assert {"headers", "rows", "total", "exported", "note"} <= set(payload)
    assert {"decision_id", "is_correct", "conservation_status"} <= set(payload["headers"])
    assert payload["exported"] <= 1000
    assert isinstance(payload["rows"], list)


def test_audit_export_handles_fresh_state_without_crashing() -> None:
    reset_sdk_scorer()

    export = client.get("/api/s2p/audit/export")
    csv = client.get("/api/s2p/audit/export/csv")

    assert export.status_code == 200
    assert csv.status_code == 200
    assert csv.json()["exported"] >= 0


def test_audit_export_does_not_write_to_graph_store() -> None:
    original_graph = app.state.graph_store
    original_scorer = app.state.scorer
    original_reader = app.state.s2p_graph_reader
    fake_graph = ReadOnlyGraphStore(
        [
            {
                "decision_id": "AUDIT-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "category": "price_variance",
                "recommended_action": "hold_for_review",
                "confidence": 0.91,
                "is_correct": True,
                "conservation_status": "GREEN",
                "amount": 100.0,
                "amount_at_risk": 10.0,
            }
        ]
    )
    app.state.graph_store = fake_graph
    app.state.scorer = SimpleNamespace(graph_store=fake_graph, get_phase=lambda: "GREEN", trajectory=lambda: {})
    app.state.s2p_graph_reader = S2PGraphReader(store=fake_graph)
    try:
        assert client.get("/api/s2p/audit/export").status_code == 200
        assert client.get("/api/s2p/audit/export/csv").status_code == 200
    finally:
        app.state.graph_store = original_graph
        app.state.scorer = original_scorer
        app.state.s2p_graph_reader = original_reader

    assert fake_graph.write_calls == 0


def test_audit_export_graph_failure_returns_503() -> None:
    original_graph = app.state.graph_store
    original_scorer = app.state.scorer
    original_reader = app.state.s2p_graph_reader

    class FailingGraphStore(ReadOnlyGraphStore):
        def get_all_decisions(self, domain: str | None = None) -> list[dict]:
            raise RuntimeError("AGE unavailable")

    fake_graph = FailingGraphStore()
    app.state.graph_store = fake_graph
    app.state.scorer = SimpleNamespace(graph_store=fake_graph)
    app.state.s2p_graph_reader = S2PGraphReader(store=fake_graph)
    try:
        response = client.get("/api/s2p/audit/export")
    finally:
        app.state.graph_store = original_graph
        app.state.scorer = original_scorer
        app.state.s2p_graph_reader = original_reader

    assert response.status_code == 503


def test_audit_export_routes_are_mounted() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/s2p/audit/export" in paths
    assert "/api/s2p/audit/export/csv" in paths


def test_existing_evidence_governance_explorer_endpoints_still_work() -> None:
    reset_sdk_scorer()

    for path in (
        "/api/s2p/evidence/compliance",
        "/api/s2p/governance/conservation-proof",
        "/api/s2p/explorer/export/csv",
    ):
        assert client.get(path).status_code == 200
