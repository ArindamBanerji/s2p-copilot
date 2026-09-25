"""Demo contracts exercised with the real scorer, audit writer and stores."""
from collections.abc import Iterator
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from fastapi import Depends

from app.routers.s2p_demo_control import router, selected_twin, budget_candidates
from app.routers.s2p_demo_beats import router as beats_router
from app.routers.s2p_evidence import router as evidence_router
from app.services.s2p_autonomy import S2PAutonomyManager
from app.services.supplier_profile_accumulator import accumulator
from app.domains.s2p.config import S2PDomainConfig
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.scoring.budget_policy import AdaptiveBudgetPolicy
from copilot_sdk.backend.investigation_router import create_investigation_router
from app.evidence_provider import S2PEvidenceProvider
from app.main import SituationClassifier, investigation_budget_context
from tests.test_s2p_audit_wiring import client_store, entries, score, outcome

COPPER = {"event_id": "S2P-INV-0001", "category": "contract_gap", "amount": 22426.73, "supplier_id": "SUP-001"}
CONTAINER = {"event_id": "S2P-DEMO-CONTAINER-01", "category": "contract_gap", "amount": 1200,
             "supplier_id": "SUP-CONTAINER-001", "context": {"working_capital": 14200, "demurrage": 1200, "days_at_port": 3}}


@pytest.fixture(autouse=True)
def isolate_telemetry() -> Iterator[None]:
    events, skipped = deepcopy(accumulator._events), accumulator.skipped_missing_supplier_id
    try:
        yield
    finally:
        accumulator._events, accumulator.skipped_missing_supplier_id = events, skipped


@pytest.fixture
def demo(client_store: tuple[TestClient, GraphStore], tmp_path: Path,
         monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, GraphStore]:
    monkeypatch.setenv("S2P_DEMO_MODE", "true")
    client, store = client_store
    client.app.state.s2p_autonomy = S2PAutonomyManager(tmp_path, client.app.state.scorer, graph_store=store)
    for mounted in (router, beats_router, evidence_router):
        client.app.include_router(mounted)
    return client, store


def test_copper_clause_and_canonical_audit(demo: tuple[TestClient, GraphStore]) -> None:
    client, store = demo
    response = client.post("/api/s2p/score", json=COPPER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "accept", body
    assert body["confidence"] > 0.8
    assert "7.3" in body["reasoning"] and body["rule_override"] is True
    assert body["canonical_action"] == entries(store)[0]["action"] == "auto_approve"
    assert body["provenance"]["planted"] is True


def test_no_clause_invented_for_unrelated_contract(demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = demo
    monkeypatch.delenv("S2P_DEMO_MODE")
    body = client.post("/api/s2p/score", json={**COPPER, "contract_id": "UNRELATED"}).json()
    assert "7.3" not in body["reasoning"]
    assert body["action"] != "accept"
    assert "provenance" not in body


def test_day_zero_is_disposable_even_with_warm_history(demo: tuple[TestClient, GraphStore]) -> None:
    client, store = demo
    prior = score(client)
    outcome(client, prior)
    before = np.asarray(client.app.state.scorer._scorer.mu).copy()
    body = client.post("/api/s2p/score", json={**COPPER, "event_id": "PW-DAY-ZERO-001"}).json()
    assert body["prior_verified_count"] == 0 and body["persistent"] is False
    assert 0 < body["confidence"] <= 1 and len(body["probabilities"]) == 5
    assert len(body["factor_vector"]) == 8 and body["decision_id"] is None
    assert store.count_decisions("s2p") == store.count_verified("s2p") == 1
    assert len(entries(store)) == 2
    np.testing.assert_array_equal(before, client.app.state.scorer._scorer.mu)


def test_day_zero_requires_explicit_mode(demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = demo
    monkeypatch.delenv("S2P_DEMO_MODE")
    assert client.post("/api/s2p/score", json={**COPPER, "event_id": "PW-DAY-ZERO-001"}).status_code == 403
    assert store.count_decisions("s2p") == 0


def test_container_has_real_hold_and_cost_context(demo: tuple[TestClient, GraphStore]) -> None:
    client, _ = demo
    body = client.post("/api/s2p/score", json=CONTAINER).json()
    assert "hold" in body["action"], body
    assert "demurrage" in body["reasoning"] and "working-capital" in body["reasoning"]
    assert body["context"]["working_capital"] == 14200


def test_container_does_not_override_explicit_factors(demo: tuple[TestClient, GraphStore]) -> None:
    client, _ = demo
    body = client.post("/api/s2p/score", json={**CONTAINER, "duplicate_score": 1.0}).json()
    assert body["factor_vector"][2] == 1.0
    assert body["canonical_action"] == body["action"]


def test_verified_format_queue_earns_persisted_extinction(demo: tuple[TestClient, GraphStore]) -> None:
    client, store = demo
    first = client.post("/api/s2p/score", json={**COPPER, "event_id": "FORMAT-A", "category": "format_compliance"}).json()
    last = client.post("/api/s2p/score", json={**COPPER, "event_id": "FORMAT-B", "category": "format_compliance"}).json()
    outcome(client, first)
    assert client.get("/api/s2p/evidence/compliance").json()["extinct_classes"] == []
    outcome(client, last)
    event = client.get("/api/s2p/evidence/compliance").json()["extinct_classes"][0]
    assert event["earning_decision_id"] == last["decision_id"]
    assert event["start_count"] > 0 and event["end_count"] == 0
    assert store.get_decision(event["earning_decision_id"], domain="s2p")["status"] == "confirmed"


def test_absence_of_scores_does_not_fabricate_extinction(demo: tuple[TestClient, GraphStore]) -> None:
    client, _ = demo
    client.post("/api/s2p/score", json={**COPPER, "event_id": "UNRESOLVED", "category": "format_compliance"})
    assert client.get("/api/s2p/evidence/compliance").json()["extinct_classes"] == []


def test_live_boundary_allocates_more_reads_than_easy(demo: tuple[TestClient, GraphStore]) -> None:
    client, _ = demo
    body = client.get("/api/demo/s2p/budget-candidates").json()["candidates"]
    policy = AdaptiveBudgetPolicy(safety_lambda=0.0)
    easy, hard = body["easy"], body["hard"]
    assert easy["confidence"] >= 0.85 and hard["confidence"] < 0.65
    assert policy.allocate(easy["confidence"], "contract_gap", 40) < policy.allocate(hard["confidence"], "contract_gap", 40)
    for candidate in (easy, hard):
        scored = client.post("/api/s2p/score", json={**COPPER, "event_id": "BOUNDARY-" + candidate["action"],
                                                   "supplier_id": "BOUNDARY",
                                                   "supplier_risk_rating": 1 - candidate["factors"]["supplier_exception_history"],
                                                   **candidate["factors"]}).json()
        assert scored["confidence"] == pytest.approx(candidate["confidence"])


def test_candidates_guard_and_category_validation(demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = demo
    assert client.get("/api/demo/s2p/budget-candidates?category=missing").status_code == 422
    monkeypatch.delenv("S2P_DEMO_MODE")
    assert client.get("/api/demo/s2p/budget-candidates").status_code == 403
    assert store.count_decisions("s2p") == 0


def test_refreeze_archives_without_replacing_production(demo: tuple[TestClient, GraphStore]) -> None:
    client, _ = demo
    manager = client.app.state.s2p_autonomy
    manager.freeze()
    path = manager.twin.store.base_dir / "s2p.json"
    original = path.read_bytes()
    response = client.post("/api/demo/s2p/learning/re-freeze", json={"confirm_demo_reseed": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provenance"]["origin"] == "demo_reseed"
    assert body["provenance"]["archived_previous"] is True
    archive = manager.twin.store.base_dir / "demo-archive" / (body["provenance"]["archive_id"] + ".json")
    assert json.loads(archive.read_text()) == json.loads(original)
    assert path.read_bytes() == original
    assert selected_twin(manager).get_snapshot().checksum == body["checksum"]
    assert client.get("/api/s2p/learning/frozen-twin").json()["provenance"] == body["provenance"]
    with pytest.raises(FileExistsError):
        manager.freeze()


def test_refreeze_requires_mode_confirmation_and_initial_snapshot(demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = demo
    url = "/api/demo/s2p/learning/re-freeze"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"confirm_demo_reseed": True}).status_code == 409
    monkeypatch.delenv("S2P_DEMO_MODE")
    assert client.post(url, json={"confirm_demo_reseed": True}).status_code == 403


def test_post_freeze_review_produces_measured_accuracy_gap(demo: tuple[TestClient, GraphStore]) -> None:
    client, _ = demo
    manager = client.app.state.s2p_autonomy
    # Real positive history before the real override; no gate mocking.
    for index in range(30):
        category = S2PDomainConfig.categories[index % 5]
        factors = budget_candidates(client.app.state.scorer, category)["easy"]["factors"]
        decision = client.post("/api/s2p/score", json={**COPPER, "event_id": f"REVIEW-WARM-{index}",
            "category": category, "supplier_id": "REVIEW-WARM", **factors,
            "supplier_risk_rating": 1 - factors["supplier_exception_history"]}).json()
        assert outcome(client, decision)["learning_applied"] is True
    manager.freeze()
    assert client.post("/api/demo/s2p/learning/re-freeze", json={"confirm_demo_reseed": True}).status_code == 200
    candidate = client.get("/api/demo/s2p/budget-candidates").json()["candidates"]["review"]
    decision = client.post("/api/s2p/score", json={**COPPER, "event_id": "REVIEW-FLIP", "supplier_id": "REVIEW",
        **candidate["factors"], "supplier_risk_rating": 1 - candidate["factors"]["supplier_exception_history"]}).json()
    assert decision["action"] == "auto_approve"
    learned = outcome(client, decision, "hold_for_review")
    assert learned["learning_applied"] is True, learned
    twin = client.get("/api/s2p/learning/frozen-twin").json()
    assert twin["delta_accuracy"] > 0, twin
    assert twin["compared_decisions"] == 31
    assert any(item["live"]["action"] == "hold_for_review" and item["frozen"]["action"] == "auto_approve"
               for item in twin["visual_diff"])


def test_demo_selection_reloads_and_disable_restores_production(demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = demo
    manager = client.app.state.s2p_autonomy
    manager.freeze()
    production = manager.twin.get_snapshot().checksum
    first = client.post("/api/demo/s2p/learning/re-freeze", json={"confirm_demo_reseed": True}).json()
    second = client.post("/api/demo/s2p/learning/re-freeze", json={"confirm_demo_reseed": True}).json()
    assert second["provenance"]["previous_checksum"] == first["checksum"]
    assert selected_twin(manager).get_snapshot().checksum == second["checksum"]
    monkeypatch.delenv("S2P_DEMO_MODE")
    assert selected_twin(manager).get_snapshot().checksum == production


def test_scored_boundary_ids_reach_actual_budget_policy(demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch) -> None:
    client, store = demo
    scorer = client.app.state.scorer
    # Populate genuine persisted outcomes, with no classifier/gate doubles.
    for index in range(30):
        decision = store.write_decision(domain="s2p", category=S2PDomainConfig.categories[index % 5],
                                        action="auto_approve", confidence=0.9, factors={})
        store.write_outcome(decision, "auto_approve", True, domain="s2p")
    classifier = SituationClassifier()
    policy = AdaptiveBudgetPolicy(safety_lambda=0.0)
    monkeypatch.setattr(classifier, "_budget_policy", policy)
    monkeypatch.setattr(classifier, "_verified_count_provider", scorer.get_verified_count)
    client.app.include_router(create_investigation_router(
        scorer_provider=lambda: scorer,
        evidence_provider_factory=lambda decision_id: S2PEvidenceProvider({}, decision_id),
        classifier=classifier, factor_names=list(S2PDomainConfig.factors), default_budget=2,
    ), dependencies=[Depends(investigation_budget_context)])
    candidates = budget_candidates(scorer, "contract_gap")
    ids = []
    for profile in ("easy", "hard"):
        factors = candidates[profile]["factors"]
        decision = client.post("/api/s2p/score", json={**COPPER, "event_id": f"BUDGET-{profile}",
            "supplier_id": "BUDGET", **factors, "supplier_risk_rating": 1 - factors["supplier_exception_history"]}).json()
        ids.append(decision["decision_id"])
        response = client.post("/api/investigation/investigate", json={
            "decision_id": decision["decision_id"], "category": decision["category"],
            "factor_vector": decision["factor_vector"], "use_K": False})
        assert response.status_code == 200, response.text
    assert [row["decision_id"] for row in policy._history] == ids
    assert [row["reads_allocated"] for row in policy._history] == [1, 4]
