"""Restart and preseed regressions using real HTTP, scorers, and stores."""
from collections.abc import Iterator
from copy import deepcopy
import importlib.util
from pathlib import Path
import socket
from threading import Thread
import time
from typing import Any

import numpy as np
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import main
from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import S2PGraphReader
from app.routers import s2p
from app.routers.s2p_demo_control import router, selected_twin, budget_candidates
from app.routers.s2p_demo_beats import router as beats_router
from app.routers.s2p_evidence import router as evidence_router
from app.services.extinction_evidence import extinction_history
from app.services.s2p_autonomy import S2PAutonomyManager
from app.services.supplier_profile_accumulator import accumulator
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.graph.sqlite_store import SQLiteGraphStore
from copilot_sdk.scoring.startup_restore import restore_l5_runtime_state
from tests.test_s2p_audit_wiring import client_store, entries, outcome

_spec = importlib.util.spec_from_file_location(
    "preseed_s2p_restart", Path(__file__).resolve().parents[2] / "scripts/preseed_s2p_demo.py")
assert _spec is not None and _spec.loader is not None
preseed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preseed)
COPPER = {"event_id": "S2P-INV-0001", "category": "contract_gap", "amount": 22426.73, "supplier_id": "SUP-001"}


@pytest.fixture
def demo(client_store: tuple[TestClient, GraphStore], tmp_path: Path,
         monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, GraphStore]]:
    monkeypatch.setenv("S2P_DEMO_MODE", "true")
    monkeypatch.delenv("S2P_DEMO_READ_MODE", raising=False)
    client, store = client_store
    assert isinstance(client.app, FastAPI)
    client.app.state.s2p_autonomy = S2PAutonomyManager(tmp_path, client.app.state.scorer, graph_store=store)
    for mounted in (router, beats_router, evidence_router):
        client.app.include_router(mounted)
    events, skipped = deepcopy(accumulator._events), accumulator.skipped_missing_supplier_id
    try:
        yield client, store
    finally:
        accumulator._events, accumulator.skipped_missing_supplier_id = events, skipped


@pytest.fixture
def seed_api(demo: tuple[TestClient, GraphStore]) -> Iterator[tuple[str, TestClient, GraphStore]]:
    client, store = demo
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    api = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(client.app, lifespan="off", log_level="error"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield api, client, store
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


def test_read_mode_keeps_presentation_but_denies_demo_controls(
    demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = demo
    assert isinstance(client.app, FastAPI)
    manager = client.app.state.s2p_autonomy
    manager.freeze()
    production = (manager.twin.store.base_dir / "s2p.json").read_bytes()
    selected = client.post("/api/demo/s2p/learning/re-freeze", json={"confirm_demo_reseed": True}).json()
    monkeypatch.delenv("S2P_DEMO_MODE")
    monkeypatch.setenv("S2P_DEMO_READ_MODE", "true")
    assert selected_twin(manager).get_snapshot().checksum == selected["checksum"]
    copper = client.post("/api/s2p/score", json=COPPER).json()
    assert copper["action"] == "accept" and copper["confidence"] > 0.8
    assert "7.3" in copper["reasoning"]
    assert entries(store)[0]["action"] == copper["canonical_action"] == "auto_approve"
    count = store.count_decisions("s2p")
    cold = client.post("/api/s2p/score", json={**COPPER, "event_id": "PW-DAY-ZERO-001"}).json()
    assert cold["prior_verified_count"] == 0 and cold["decision_id"] is None
    assert cold["persistent"] is False and len(cold["probabilities"]) == 5
    assert store.count_decisions("s2p") == count
    for path in ("budget-candidates", "seed-decisions"):
        assert client.get("/api/demo/s2p/" + path).status_code == 403
    assert client.post("/api/demo/s2p/learning/re-freeze", json={"confirm_demo_reseed": True}).status_code == 403
    assert client.post("/api/demo/s2p/learning/persist").status_code == 403
    assert (manager.twin.store.base_dir / "s2p.json").read_bytes() == production
    monkeypatch.delenv("S2P_DEMO_READ_MODE")
    assert selected_twin(manager).get_snapshot().checksum == manager.twin.get_snapshot().checksum


def test_read_mode_cannot_bypass_normal_conservation(
    demo: tuple[TestClient, GraphStore], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = demo
    monkeypatch.delenv("S2P_DEMO_MODE")
    monkeypatch.setenv("S2P_DEMO_READ_MODE", "true")
    for index in range(25):
        decision = store.write_decision(domain="s2p", category=S2PDomainConfig.categories[index % 5],
            action="auto_approve", confidence=0.9, factors={})
        store.write_outcome(decision, "hold_for_review", False, domain="s2p")
    s2p._clear_score_conservation_status_cache()
    assert s2p._score_conservation_status(Request({"type": "http", "app": client.app})) == "RED"
    response = client.post("/api/s2p/score", json=COPPER)
    assert response.status_code == 503 and response.json()["detail"]["reason"] == "conservation_red"
    assert store.count_decisions("s2p") == 25


def test_real_learning_gap_survives_scorer_and_store_reopen(
    demo: tuple[TestClient, GraphStore], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store = demo
    assert isinstance(client.app, FastAPI)
    for index in range(30):
        category = S2PDomainConfig.categories[index % 5]
        factors = budget_candidates(client.app.state.scorer, category)["easy"]["factors"]
        decision = client.post("/api/s2p/score", json={**COPPER, "event_id": f"RESTART-{index}",
            "category": category, "supplier_id": "SUP-RESTART", **factors,
            "supplier_risk_rating": 1 - factors["supplier_exception_history"]}).json()
        assert outcome(client, decision)["learning_applied"] is True
    manager = client.app.state.s2p_autonomy
    manager.freeze()
    assert client.post("/api/demo/s2p/learning/re-freeze", json={"confirm_demo_reseed": True}).status_code == 200
    factors = budget_candidates(client.app.state.scorer, "contract_gap")["review"]["factors"]
    decision = client.post("/api/s2p/score", json={**COPPER, "event_id": "RESTART-REVIEW",
        "supplier_id": "SUP-RESTART", **factors,
        "supplier_risk_rating": 1 - factors["supplier_exception_history"]}).json()
    assert outcome(client, decision, "hold_for_review")["learning_applied"] is True
    before = client.get("/api/s2p/learning/frozen-twin").json()
    assert before["delta_accuracy"] > 0
    expected_mu = client.app.state.scorer._scorer.mu.copy()
    persisted = client.post("/api/demo/s2p/learning/persist")
    assert persisted.status_code == 200 and persisted.json()["centroid_cells"] == 25
    reopened = SQLiteGraphStore(tmp_path / "score-audit.db", domain="s2p") if isinstance(store, SQLiteGraphStore) else store
    try:
        restored = main.build_s2p_scorer(graph_store=reopened, profile="test")
        restore_l5_runtime_state(domain="s2p", scorer=restored, learning_store=reopened)
        np.testing.assert_allclose(restored._scorer.mu, expected_mu)
        client.app.state.scorer = restored
        client.app.state.graph_store = reopened
        client.app.state.s2p_graph_reader = S2PGraphReader(reopened)
        client.app.state.s2p_autonomy = S2PAutonomyManager(tmp_path, restored, graph_store=reopened)
        monkeypatch.delenv("S2P_DEMO_MODE")
        monkeypatch.setenv("S2P_DEMO_READ_MODE", "true")
        after = client.get("/api/s2p/learning/frozen-twin").json()
        assert after["provenance"] == before["provenance"]
        assert after["visual_diff"] == before["visual_diff"]
        assert after["delta_accuracy"] == before["delta_accuracy"]
    finally:
        if reopened is not store:
            reopened.close()


def test_complete_seed_lookup_exceeds_public_limit(seed_api: tuple[str, TestClient, GraphStore]) -> None:
    api, _, store = seed_api
    for index in range(505):
        store.write_decision(domain="s2p", category="price_variance", action="auto_approve", confidence=0.9,
            factors={}, metadata={"invoice_id": f"INVENTORY-{index}"})
    inventory = preseed._existing_decisions(api)
    assert len(inventory) == 505 and "INVENTORY-504" in inventory


def test_seed_resumes_legacy_pending_and_earns_new_planted_extinction(
    seed_api: tuple[str, TestClient, GraphStore],
) -> None:
    api, _, store = seed_api
    report = preseed.run(api, dry_run=True)
    legacy = store.write_decision(domain="s2p", category="format_compliance", action="auto_approve",
        confidence=0.9, factors={name: float(value) for name, value in zip(S2PDomainConfig.factors,
            [0.98, 0.01, 0.02, 0.5, 0.2, 0.9, 0.98, 0.1])},
        metadata={"invoice_id": "S2P-DEMO-WARM-004", "supplier_id": "SUP-WARM-004"})
    original = deepcopy(store.get_decision(legacy, domain="s2p"))
    preseed._seed_extinction(api, preseed._existing_decisions(api), report)
    assert not report["errors"] and not report["learning_errors"] and not report["limitations"], report
    events = extinction_history(store)
    assert len(events) == 1 and events[0]["earning_decision_id"] != legacy
    assert events[0]["planted"] is True and events[0]["evidence_tier"] == "T_S"
    assert events[0]["start_count"] == 1 and events[0]["end_count"] == 0
    persisted = store.get_decision(legacy, domain="s2p")
    assert persisted is not None and original is not None
    assert persisted["metadata"] == original["metadata"]
    assert all(entry["decision_id"] != legacy for entry in entries(store))
    count = store.count_decisions("s2p")
    preseed._seed_extinction(api, preseed._existing_decisions(api), report)
    assert store.count_decisions("s2p") == count and len(extinction_history(store)) == 1


def test_seed_refuses_unrelated_pending_format(seed_api: tuple[str, TestClient, GraphStore]) -> None:
    api, _, store = seed_api
    pending = store.write_decision(domain="s2p", category="format_compliance", action="auto_approve",
        confidence=0.9, factors={}, metadata={"invoice_id": "REAL-INVOICE", "supplier_id": "REAL-SUPPLIER"})
    report = preseed.run(api, dry_run=True)
    preseed._seed_extinction(api, {}, report)
    assert pending in report["limitations"][0]
    persisted = store.get_decision(pending, domain="s2p")
    assert persisted is not None and persisted["status"] == "pending"
    assert store.count_decisions("s2p") == 1 and extinction_history(store) == []
