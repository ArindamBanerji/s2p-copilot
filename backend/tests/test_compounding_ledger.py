"""F24 Compounding Ledger contract tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as s2p_app
from app.routers.s2p_ledger import create_ledger_router
from app.services.compounding_ledger import CompoundingLedger
from app.services.proposal_service import ProposalService, ProposalStore
from copilot_sdk.graph.memory_store import InMemoryGraphStore


class LedgerGraphStore(InMemoryGraphStore):
    """AGE-shaped event reads for ledger tests: filtered and newest first."""

    def get_evolution_events(self, domain: str, **kwargs: object) -> list[dict]:
        event_type = kwargs.get("event_type")
        events = super().get_evolution_events(domain, limit=kwargs.get("limit", 100))
        if isinstance(event_type, str):
            events = [event for event in events if event.get("event_type") == event_type]
        return list(reversed(events))


def _score(decision_id: str = "decision-1") -> dict:
    return {
        "decision_id": decision_id,
        "category": "price_variance",
        "action": "hold_for_review",
        "confidence": 0.84,
    }


def _evidence(impact: float | None = None) -> dict:
    result = {
        "evidence_chain": [{"source": "profile_scorer", "finding": "nearest centroid", "weight": 0.8}],
        "similar_decisions": ["decision-previous-1"],
    }
    if impact is not None:
        result["expected_kpi_delta"] = {"savings_usd": impact}
    return result


def _services(tmp_path: Path) -> tuple[ProposalService, CompoundingLedger]:
    proposals = ProposalStore(str(tmp_path / "proposals.sqlite3"))
    service = ProposalService(proposals)
    ledger = CompoundingLedger(service.store, LedgerGraphStore(domain="s2p"))
    return service, ledger


def _proposal(service: ProposalService, invoice_id: str = "invoice-1", impact: float | None = None):
    return service.create_from_score(
        _score(invoice_id), invoice_id, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.5], _evidence(impact)
    )


def _client(ledger: CompoundingLedger) -> TestClient:
    api = FastAPI()
    api.include_router(create_ledger_router(ledger))
    return TestClient(api)


def test_cl_01_timeline_includes_proposals_and_outcomes(tmp_path):
    service, ledger = _services(tmp_path)
    proposal = _proposal(service)
    service.confirm(proposal.proposal_id)
    events = ledger.timeline()
    assert {event["event_type"] for event in events} == {"proposal", "outcome"}
    assert all(event["proposal_id"] == proposal.proposal_id for event in events)


def test_cl_02_timeline_entries_have_evidence_tiers(tmp_path):
    service, ledger = _services(tmp_path)
    proposal = _proposal(service)
    service.confirm(proposal.proposal_id)
    assert {event["evidence_tier"] for event in ledger.timeline()} == {"T_S", "T_O"}


def test_cl_03_summary_aggregates_financial_impact(tmp_path):
    service, ledger = _services(tmp_path)
    proposal = _proposal(service, impact=125.0)
    service.confirm(proposal.proposal_id)
    summary = ledger.summary()
    assert summary["total_impact"] == 125.0
    assert summary["per_category"] == {}
    assert summary["measured_impact_count"] == 1


def test_cl_04_iks_trajectory_returns_time_series(tmp_path):
    _service, ledger = _services(tmp_path)
    ledger.record_iks(42.0, observed_at="2026-01-01T00:00:00Z")
    ledger.record_iks(51.0, observed_at="2026-01-02T00:00:00Z")
    assert [point["iks_value"] for point in ledger.iks_trajectory()] == [42.0, 51.0]


def test_cl_05_conservation_history_returns_state_changes(tmp_path):
    _service, ledger = _services(tmp_path)
    ledger.record_conservation({"phase": "GREEN", "alpha": 0.8, "q": 0.9}, observed_at="2026-01-01T00:00:00Z")
    ledger.record_conservation({"phase": "AMBER", "alpha": 0.5, "q": 0.7}, observed_at="2026-01-02T00:00:00Z")
    assert [point["phase"] for point in ledger.conservation_history()] == ["GREEN", "AMBER"]


def test_cl_06_timeline_respects_limit(tmp_path):
    service, ledger = _services(tmp_path)
    _proposal(service, "invoice-1")
    _proposal(service, "invoice-2")
    assert len(ledger.timeline(1)) == 1


def test_cl_07_empty_ledger_returns_empty_arrays(tmp_path):
    _service, ledger = _services(tmp_path)
    assert ledger.timeline() == []
    assert ledger.iks_trajectory() == []
    assert ledger.conservation_history() == []
    assert ledger.summary()["total_impact"] == 0.0


def test_cl_08_summary_excludes_synthetic_unverified_entries(tmp_path):
    service, ledger = _services(tmp_path)
    _proposal(service, impact=999.0)
    summary = ledger.summary()
    assert summary["verified_outcomes"] == 0
    assert summary["total_impact"] == 0.0
    assert summary["measured_impact_count"] == 0


def test_cl_09_per_category_breakdown_matches_entries(tmp_path):
    service, ledger = _services(tmp_path)
    first = _proposal(service, "invoice-1", 10.0)
    second = service.create_from_score(
        {**_score("invoice-2"), "category": "duplicate_invoice"}, "invoice-2", [0.1], _evidence(20.0)
    )
    service.confirm(first.proposal_id)
    service.confirm(second.proposal_id)
    assert ledger.summary()["per_category"] == {}


def test_cl_10_router_timeline(tmp_path):
    service, ledger = _services(tmp_path)
    _proposal(service)
    response = _client(ledger).get("/api/s2p/ledger/timeline", params={"limit": 10})
    assert response.status_code == 200
    assert len(response.json()["timeline"]) == 1


def test_cl_11_router_summary(tmp_path):
    service, ledger = _services(tmp_path)
    proposal = _proposal(service, impact=25.0)
    service.confirm(proposal.proposal_id)
    response = _client(ledger).get("/api/s2p/ledger/summary")
    assert response.status_code == 200
    assert response.json()["total_impact"] == 25.0


def test_cl_12_router_iks_trajectory(tmp_path):
    _service, ledger = _services(tmp_path)
    ledger.record_iks(30.0)
    response = _client(ledger).get("/api/s2p/ledger/iks-trajectory")
    assert response.status_code == 200
    assert response.json()["trajectory"][0]["iks_value"] == 30.0


def test_cl_13_router_conservation_history(tmp_path):
    _service, ledger = _services(tmp_path)
    ledger.record_conservation({"status": "GREEN", "q": 0.9})
    response = _client(ledger).get("/api/s2p/ledger/conservation-history")
    assert response.status_code == 200
    assert response.json()["history"][0]["status"] == "GREEN"


def test_cl_14_verified_outcomes_are_observed(tmp_path):
    service, ledger = _services(tmp_path)
    proposal = _proposal(service)
    service.confirm(proposal.proposal_id)
    outcome = next(event for event in ledger.timeline() if event["event_type"] == "outcome")
    assert outcome["evidence_tier"] == "T_O"
    assert outcome["correct"] is True


def test_cl_15_unresolved_proposals_are_synthetic(tmp_path):
    service, ledger = _services(tmp_path)
    _proposal(service)
    event = ledger.timeline()[0]
    assert event["evidence_tier"] == "T_S"
    assert event["correct"] is None


def test_cl_16_concurrent_reads_are_safe(tmp_path):
    service, ledger = _services(tmp_path)
    proposal = _proposal(service)
    service.confirm(proposal.proposal_id)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: ledger.summary(), range(16)))
    assert all(result["verified_outcomes"] == 1 for result in results)


def test_cl_17_output_is_json_safe(tmp_path):
    service, ledger = _services(tmp_path)
    proposal = _proposal(service, impact=10.0)
    service.confirm(proposal.proposal_id)
    json.dumps(ledger.timeline())
    json.dumps(ledger.summary())
    assert all(value is not None for value in ledger.summary()["per_category"].values())


def test_cl_18_ledger_survives_service_restart(tmp_path):
    _service, first = _services(tmp_path)
    first.record_iks(77.0, observed_at="2026-01-01T00:00:00Z")
    first.record_conservation({"phase": "GREEN"}, observed_at="2026-01-01T00:00:00Z")
    first.close()
    proposals = ProposalStore(str(tmp_path / "proposals.sqlite3"))
    second = CompoundingLedger(proposals, first.graph_store)
    assert second.iks_trajectory()[0]["iks_value"] == 77.0
    assert second.conservation_history()[0]["phase"] == "GREEN"


def test_cl_19_score_propose_confirm_ledger_entry():
    event_id = "F24-CL19-INTEGRATION"
    response = TestClient(s2p_app).post(
        "/api/s2p/score",
        json={
            "event_id": event_id,
            "category": "price_variance",
            "amount": 2500.0,
            "supplier_id": "SUP-CL19",
            "match_status": 0.92,
            "amount_variance_ratio": 0.08,
            "duplicate_score": 0.04,
            "supplier_exception_history": 0.05,
            "payment_terms_impact": 0.48,
            "commodity_index_correlation": 0.76,
            "tax_regulatory_compliance": 0.90,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    proposal_id = payload["proposal_id"]
    s2p_app.state.proposal_service.confirm(proposal_id)
    events = s2p_app.state.compounding_ledger.timeline()
    assert any(event["proposal_id"] == proposal_id and event["event_type"] == "outcome" for event in events)
