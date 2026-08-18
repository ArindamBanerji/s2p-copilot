"""F23 Decision-Change Proposal tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domains.s2p.proposals import DecisionChangeProposal
from app.main import app as s2p_app
from app.routers.s2p_proposals import create_proposal_router
from app.services.proposal_service import ProposalService, ProposalStore


def _score(decision_id: str = "decision-1", action: str = "hold_for_review") -> dict:
    return {
        "decision_id": decision_id,
        "category": "price_variance",
        "action": action,
        "confidence": 0.84,
    }


def _evidence() -> dict:
    return {
        "evidence_chain": [
            {"source": "profile_scorer", "finding": "nearest centroid", "weight": 0.8},
            {"source": "similar_decisions", "finding": "three matching invoices", "weight": 0.6},
        ],
        "similar_decisions": ["decision-previous-1"],
        "expected_kpi_delta": {"exception_rate": -0.12},
        "rollback_path": {"action": "hold_for_review", "reason": "verified outcome degraded"},
    }


def _service(tmp_path: Path) -> ProposalService:
    return ProposalService(ProposalStore(str(tmp_path / "proposals.sqlite3")))


def _proposal(service: ProposalService, invoice_id: str = "invoice-1") -> DecisionChangeProposal:
    return service.create_from_score(_score(), invoice_id, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], _evidence())


def _router_client(service: ProposalService) -> TestClient:
    api = FastAPI()
    api.include_router(create_proposal_router(service))
    return TestClient(api)


def test_pr_01_create_from_score_produces_complete_proposal(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)

    assert proposal.copilot == "s2p"
    assert proposal.invoice_id == "invoice-1"
    assert proposal.decision_id == "decision-1"
    assert proposal.proposed_action == "hold_for_review"
    assert proposal.status == "proposed"
    assert proposal.factor_vector == [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    assert len(proposal.evidence_chain) == 2
    assert proposal.expected_kpi_delta == {"exception_rate": -0.12}
    assert proposal.rollback_path == {"action": "hold_for_review", "reason": "verified outcome degraded"}


def test_pr_02_store_round_trip(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    assert service.store.get(proposal.proposal_id) == proposal


def test_pr_03_get_by_invoice(tmp_path):
    service = _service(tmp_path)
    first = _proposal(service, "invoice-1")
    second = service.create_from_score(_score("decision-2"), "invoice-2", [0.2], _evidence())
    assert [item.proposal_id for item in service.store.get_by_invoice("invoice-1")] == [first.proposal_id]
    assert service.store.get_by_invoice("invoice-2")[0] == second


def test_pr_04_confirm_creates_verified_outcome(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    resolved = service.confirm(proposal.proposal_id)

    assert resolved.status == "confirmed"
    assert resolved.outcome_receipt_id
    outcome = service.store.get_outcome(proposal.proposal_id)
    assert outcome["correct"] is True
    assert outcome["human_disposition"] == "confirm"
    assert outcome["evidence_provenance"] == f"decision_change_proposal:{proposal.proposal_id}"


def test_pr_05_override_creates_incorrect_verified_outcome(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    resolved = service.override(proposal.proposal_id, "auto_approve", "buyer verified the exception is benign")

    assert resolved.status == "overridden"
    outcome = service.store.get_outcome(proposal.proposal_id)
    assert outcome["correct"] is False
    assert outcome["human_disposition"] == "override"
    assert outcome["override_action"] == "auto_approve"
    assert outcome["override_reason"] == "buyer verified the exception is benign"


def test_pr_06_link_outcome_links_receipt(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    linked = service.link_outcome(proposal.proposal_id, "receipt-external-1")
    assert linked.outcome_receipt_id == "receipt-external-1"
    assert service.store.get(proposal.proposal_id).outcome_receipt_id == "receipt-external-1"


def test_pr_07_audit_trail_contains_proposal_and_outcome(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    service.confirm(proposal.proposal_id)
    trail = service.get_audit_trail("invoice-1")

    assert len(trail) == 1
    assert trail[0]["proposal"]["proposal_id"] == proposal.proposal_id
    assert trail[0]["outcome"]["outcome_id"].startswith("s2p:decision-1:")


def test_pr_08_duplicate_proposal_id_updates_existing_record(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    updated = replace(proposal, status="expired")
    service.store.save(updated)
    assert service.store.count() == 1
    assert service.store.get(proposal.proposal_id).status == "expired"


def test_pr_09_list_recent_is_newest_first_and_limited(tmp_path):
    service = _service(tmp_path)
    first = _proposal(service, "invoice-1")
    second = service.create_from_score(_score("decision-2"), "invoice-2", [0.2], _evidence())
    recent = service.store.list_recent(1)
    assert len(recent) == 1
    assert recent[0].proposal_id == second.proposal_id
    assert recent[0].created_at >= first.created_at


def test_pr_10_count_supports_status_filter(tmp_path):
    service = _service(tmp_path)
    first = _proposal(service, "invoice-1")
    second = _proposal(service, "invoice-2")
    service.confirm(first.proposal_id)
    assert service.store.count() == 2
    assert service.store.count("confirmed") == 1
    assert service.store.count("proposed") == 1
    assert service.store.get(second.proposal_id).status == "proposed"


def test_pr_11_router_create_proposal(tmp_path):
    service = _service(tmp_path)
    client = _router_client(service)
    response = client.post("/api/s2p/proposal", json={"invoice_id": "invoice-1", "score_result": _score(), "factor_vector": [0.1], "evidence": _evidence()})
    assert response.status_code == 200
    assert response.json()["status"] == "proposed"


def test_pr_12_router_get_proposal_returns_audit_shape(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    response = _router_client(service).get(f"/api/s2p/proposal/{proposal.proposal_id}")
    assert response.status_code == 200
    assert response.json()["proposal_id"] == proposal.proposal_id
    assert response.json()["audit_trail"][0]["proposal"]["invoice_id"] == "invoice-1"


def test_pr_13_router_confirm(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    response = _router_client(service).post(f"/api/s2p/proposal/{proposal.proposal_id}/confirm")
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert response.json()["outcome_receipt_id"]


def test_pr_14_router_override(tmp_path):
    service = _service(tmp_path)
    proposal = _proposal(service)
    response = _router_client(service).post(
        f"/api/s2p/proposal/{proposal.proposal_id}/override",
        json={"override_action": "auto_approve", "reason": "human approved"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "overridden"


def test_pr_15_router_list_filters_by_status(tmp_path):
    service = _service(tmp_path)
    first = _proposal(service, "invoice-1")
    _proposal(service, "invoice-2")
    service.confirm(first.proposal_id)
    response = _router_client(service).get("/api/s2p/proposals", params={"status": "confirmed"})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["proposals"][0]["proposal_id"] == first.proposal_id


def test_pr_16_score_endpoint_creates_proposal(tmp_path):
    # Exercise the real S2P score route; proposal persistence is additive to its response.
    event_id = "F23-PR16-INTEGRATION"
    response = TestClient(s2p_app).post(
        "/api/s2p/score",
        json={
            "event_id": event_id,
            "category": "price_variance",
            "amount": 5000.0,
            "supplier_id": "SUP-PR16",
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
    assert payload["proposal_id"]
    stored = s2p_app.state.proposal_service.store.get(payload["proposal_id"])
    assert stored is not None
    assert stored.invoice_id == event_id
    assert stored.decision_id == payload["decision_id"]


def test_pr_17_concurrent_proposals_are_persisted(tmp_path):
    service = _service(tmp_path)

    def create(index: int) -> str:
        return _proposal(service, f"invoice-{index}").proposal_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create, range(24)))
    assert len(set(ids)) == 24
    assert service.store.count() == 24


def test_pr_18_proposal_survives_store_reinstantiation(tmp_path):
    path = tmp_path / "restart.sqlite3"
    first_store = ProposalStore(str(path))
    proposal = ProposalService(first_store).create_from_score(_score(), "invoice-restart", [0.1], _evidence())
    first_store.close()
    second_store = ProposalStore(str(path))
    assert second_store.get(proposal.proposal_id) == proposal
    second_store.close()


def test_pr_19_framework_category_is_preserved(tmp_path):
    service = _service(tmp_path)
    proposal = service.create_from_score(_score(), "invoice-category", [0.1, 0.2], {"category": "price_variance"})
    assert proposal.category == "price_variance"
    assert service.store.get_by_invoice("invoice-category")[0].category == "price_variance"
