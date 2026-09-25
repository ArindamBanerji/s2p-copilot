"""Real store and HTTP regressions for causal queue extinction."""
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.routers.s2p_evidence import router as evidence_router
from app.services.extinction_evidence import active_decision_ids, extinction_history, record_extinction
from app.services.supplier_profile_accumulator import accumulator
from app.state.s2p_registry import create_s2p_tab_state_cache
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.graph.sqlite_store import SQLiteGraphStore
from copilot_sdk.state import invalidation
from tests.test_s2p_audit_wiring import client_store, outcome, score


@pytest.fixture(autouse=True)
def restore_supplier_telemetry() -> Iterator[None]:
    # Real outcome routes update this process-wide accumulator. Preserve those
    # side effects during the test, then restore the previous telemetry.
    events = deepcopy(accumulator._events)
    skipped = accumulator.skipped_missing_supplier_id
    try:
        yield
    finally:
        accumulator._events = events
        accumulator.skipped_missing_supplier_id = skipped


def test_last_outcome_earns_extinction_and_response_aliases(
    client_store: tuple[TestClient, GraphStore],
) -> None:
    client, store = client_store
    client.app.include_router(evidence_router)
    first, last = score(client, "EXT-FIRST"), score(client, "EXT-LAST")
    outcome(client, first)
    assert extinction_history(store) == []
    outcome(client, last)
    events = extinction_history(store)
    assert len(events) == 1
    assert events[0]["earning_decision_id"] == last["decision_id"]
    assert events[0]["start_count"] == 1
    assert events[0]["end_count"] == 0
    response = client.get("/api/s2p/evidence/compliance")
    assert response.status_code == 200, response.text
    body = response.json()
    for alias in ("extinction_classes", "extinct_classes", "queue_extinctions"):
        assert body[alias] == events
    assert store.get_decision(events[0]["earning_decision_id"], domain="s2p")["status"] == "confirmed"


def test_learn_route_records_same_evidence(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    response = client.post("/api/learn", json={
        "decision_id": decision["decision_id"], "actual_action": decision["action"],
    })
    assert response.status_code == 200, response.text
    assert extinction_history(store)[0]["earning_decision_id"] == decision["decision_id"]


def test_persisted_extinction_survives_reopen_and_retains_history(tmp_path: Path) -> None:
    path = tmp_path / "extinction.db"
    store = SQLiteGraphStore(path, domain="s2p")
    decision = store.write_decision(
        domain="s2p", category="price_variance", action="auto_approve",
        confidence=0.9, factors={}, metadata={"planted": True},
    )
    before = active_decision_ids(store, "price_variance")
    assert record_extinction(store, "price_variance", decision, before) is None
    store.write_outcome(decision, "auto_approve", True, domain="s2p")
    event = record_extinction(store, "price_variance", decision, before)
    assert event is not None and event["planted"] is True and event["evidence_tier"] == "T_S"
    duplicate = record_extinction(store, "price_variance", decision, before)
    assert duplicate is not None
    assert duplicate["recorded_at"] == event["recorded_at"]
    store.close()
    store = SQLiteGraphStore(path, domain="s2p")
    try:
        assert extinction_history(store)[0]["earning_decision_id"] == decision
        reopened = store.write_decision(domain="s2p", category="price_variance",
            action="auto_approve", confidence=0.9, factors={})
        assert active_decision_ids(store, "price_variance") == {reopened}
        assert len(extinction_history(store)) == 1  # Historical event, not permanent extinction.
    finally:
        store.close()


def test_no_extinction_without_last_active_transition(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    assert record_extinction(store, "price_variance", decision["decision_id"], set()) is None
    assert extinction_history(store) == []


def test_unrelated_category_or_bulk_drain_cannot_claim_causality(
    client_store: tuple[TestClient, GraphStore],
) -> None:
    _, store = client_store
    ids = {
        store.write_decision(domain="s2p", category="price_variance",
                             action="auto_approve", confidence=0.9, factors={})
        for _ in range(2)
    }
    for decision_id in ids:
        store.write_outcome(decision_id, "auto_approve", True, domain="s2p")
    last = next(iter(ids))
    assert record_extinction(store, "price_variance", last, ids) is None
    assert record_extinction(store, "contract_gap", last, {last}) is None
    assert extinction_history(store) == []


def test_warm_compliance_cache_reflects_new_extinction(
    client_store: tuple[TestClient, GraphStore],
) -> None:
    client, _ = client_store
    client.app.include_router(evidence_router)
    previous = invalidation.get_tab_state_cache("s2p")
    cache = create_s2p_tab_state_cache(client.app.state)
    client.app.state.s2p_tab_state_cache = cache
    try:
        decision = score(client, "EXT-CACHE")
        before = client.get("/api/s2p/evidence/compliance")
        assert before.status_code == 200, before.text
        assert before.json()["extinction_classes"] == []
        assert cache.get_entry("evidence-compliance") is not None
        outcome(client, decision)
        after = client.get("/api/s2p/evidence/compliance")
        assert after.status_code == 200, after.text
        assert after.json()["extinction_classes"][0]["earning_decision_id"] == decision["decision_id"]
    finally:
        if previous is None:
            invalidation._CACHES.pop("s2p", None)
        else:
            invalidation.register_tab_state_cache(previous)
