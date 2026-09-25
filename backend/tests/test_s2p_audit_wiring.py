"""HTTP-to-persistence audit contracts using real scorers and GraphStore adapters."""
from dataclasses import fields
import json
from pathlib import Path
from threading import Barrier
from typing import Any, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.domains.s2p.config import S2PDomainConfig
from app.framework import audit
from app.graph.s2p_graph_reader import S2PGraphReader
from app.routers import s2p
from app.routers.s2p_audit_export import router as audit_router
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.graph.sqlite_store import SQLiteGraphStore


@pytest.fixture(params=["memory", "sqlite"])
def client_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[tuple[TestClient, GraphStore]]:
    store = (InMemoryGraphStore(domain="s2p") if request.param == "memory"
             else SQLiteGraphStore(tmp_path / "score-audit.db", domain="s2p"))
    scorer = main.build_s2p_scorer(graph_store=store, profile="test")
    app = FastAPI()
    app.state.scorer = scorer
    app.state.graph_store = store
    app.state.s2p_graph_reader = S2PGraphReader(store)
    app.state.s2p_reward_function = scorer._reward_fn
    app.state.audit_writer = s2p.S2PScoreAuditWriter()
    app.include_router(s2p.router)
    app.include_router(s2p.learn_router)
    app.include_router(audit_router)
    s2p._clear_score_conservation_status_cache()
    with TestClient(app) as client:
        try:
            yield client, store
        finally:
            # Real score routes schedule advisory graph links. Wait for preceding
            # work on every worker before closing the database; do not patch out
            # side effects to make an artificial synchronous scoring path.
            workers = s2p._SIDE_EFFECT_EXECUTOR._max_workers
            barrier = Barrier(workers + 1, timeout=20)
            futures = [s2p._SIDE_EFFECT_EXECUTOR.submit(barrier.wait) for _ in range(workers)]
            barrier.wait()
            for future in futures:
                future.result(timeout=20)
            store.close()


def score(client: TestClient, event: str = "AUDIT-WIRING-001") -> dict[str, Any]:
    response = client.post("/api/s2p/score", json={
        "event_id": event, "category": "price_variance", "amount": 1000.0,
        "supplier_id": "SUP-AUDIT", "match_status": 0.9, "amount_variance_ratio": 0.2,
        "duplicate_score": 0.1, "supplier_exception_history": 0.1,
        "payment_terms_impact": 0.2, "commodity_index_correlation": 0.3,
        "tax_regulatory_compliance": 0.9,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision_id"]
    return body


def outcome(client: TestClient, decision: dict[str, Any], action: str | None = None) -> dict[str, Any]:
    actual_action = action or decision["action"]
    override = actual_action != decision["action"]
    response = client.post("/api/s2p/outcome", json={
        "decision_id": decision["decision_id"], "outcome": "override" if override else "confirm",
        "analyst_action": actual_action, "analyst_id": "audit-test",
        "factor_vector": decision["factor_vector"], "category": decision["category"],
        "predicted_action": decision["action"], "reason_code": "wrong_action" if override else None,
    })
    assert response.status_code == 200, response.text
    return response.json()


def entries(store: GraphStore) -> list[dict[str, Any]]:
    return sorted([row for row in store.list_ledgers("s2p")
                   if row["key"].startswith("s2p-audit-entry:")], key=lambda row: row["chain_index"])


def test_score_creates_audit_entry(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    assert store.count_decisions("s2p") == 1  # No duplicate audit-created Decision.
    assert len(entries(store)) == 1
    assert entries(store)[0]["decision_id"] == decision["decision_id"]


def test_audit_entry_has_hash(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    entry = entries(store)[0]
    restored = audit.LedgerEntry(**{field.name: entry[field.name] for field in fields(audit.LedgerEntry)})
    assert len(entry["entry_hash"]) == 64
    assert entry["entry_hash"] == restored.compute_hash()
    assert entry["factor_breakdown"] == dict(zip(decision["factor_names"], decision["factor_vector"]))
    assert entry["action"] == decision["action"]
    assert entry["confidence"] == decision["confidence"]


def test_audit_chain_links(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    score(client, "AUDIT-A")
    score(client, "AUDIT-B")
    first, second = entries(store)
    assert first["prev_hash"] == "0" * 64
    assert second["prev_hash"] == first["entry_hash"]
    assert [first["chain_index"], second["chain_index"]] == [0, 1]


def test_verify_after_score_returns_verified(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    score(client)
    response = client.post("/api/s2p/audit/verify")
    assert response.status_code == 200
    assert response.json()["verified"] is True
    assert response.json()["entries_checked"] == 1
    assert audit.verify_chain(store)["verified"] is True


def test_outcome_creates_audit_entry(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    outcome(client, decision)
    first, second = entries(store)
    assert [first["type"], second["type"]] == ["decision", "outcome"]
    assert second["decision_entry_hash"] == first["entry_hash"]
    assert second["decision_id"] == decision["decision_id"]
    assert store.count_decisions("s2p") == store.count_verified("s2p") == 1
    receipt = json.loads(second["outcome"])
    assert receipt["actual_action"] == decision["action"]
    assert receipt["is_correct"] is True


def test_verify_after_outcome_still_verified(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    original = entries(store)[0]
    outcome(client, decision)
    assert entries(store)[0] == original
    result = client.post("/api/s2p/audit/verify").json()
    assert result["verified"] is True
    assert result["entries_checked"] == 2
    assert result["tamper_evidence"] == []


def test_tampered_score_detected(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    score(client)
    entry = entries(store)[0]
    key = entry.pop("key")
    entry["confidence"] = -0.5
    store.save_ledger("s2p", key, entry)
    result = client.post("/api/s2p/audit/verify").json()
    assert result["verified"] is False
    assert result["tamper_evidence"][0]["detail"] == "entry_hash mismatch"


def test_learn_route_also_seals_outcome(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    response = client.post("/api/learn", json={
        "decision_id": decision["decision_id"], "actual_action": decision["action"], "outcome": "confirmed",
    })
    assert response.status_code == 200, response.text
    assert len(entries(store)) == 2
    assert audit.verify_chain(store)["verified"] is True


def test_override_preserves_ground_truth(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    actual = next(action for action in S2PDomainConfig.actions if action != decision["action"])
    outcome(client, decision, actual)
    verified = store.get_verified_decisions("s2p")
    assert len(verified) == 1
    assert verified[0]["actual_action"] == actual  # Audit must not rewrite the SDK outcome.
    receipt = json.loads(entries(store)[1]["outcome"])
    assert receipt["actual_action"] == actual
    assert receipt["is_correct"] is False
    assert audit.verify_chain(store)["verified"] is True


def test_invalid_score_creates_no_audit_entry(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    assert client.post("/api/s2p/score", json={}).status_code == 422
    assert store.count_decisions("s2p") == 0
    assert entries(store) == []
    assert audit.verify_chain(store)["verified"] is True


def test_unknown_outcome_does_not_poison_chain(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    response = client.post("/api/learn", json={
        "decision_id": "does-not-exist", "actual_action": S2PDomainConfig.actions[0],
    })
    assert response.status_code == 404
    assert audit.verify_chain(store)["verified"] is True


def test_sealing_failure_is_not_success(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    decision = score(client)
    row = entries(store)[0]
    key = row.pop("key")
    row["action"] = "tampered"
    store.save_ledger("s2p", key, row)
    response = client.post("/api/learn", json={
        "decision_id": decision["decision_id"], "actual_action": decision["action"],
    })
    assert response.status_code == 503
    result = audit.verify_chain(store)
    assert result["verified"] is False
    assert result["incomplete_writes"] == 1


def test_legacy_history_is_not_retroactively_sealed(client_store: tuple[TestClient, GraphStore]) -> None:
    client, store = client_store
    store.write_decision("s2p", "price_variance", S2PDomainConfig.actions[0], 0.8, {"match_status": 0.8})
    score(client)
    result = audit.verify_chain(store)
    assert result["verified"] is False
    assert result["reason"] == "verification_unavailable"
    assert result["unsealed_decisions"] == 1


def test_profile_from_env_not_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("S2P_PROFILE", raising=False)
    monkeypatch.delenv("GRAPH_PROFILE", raising=False)
    monkeypatch.delenv("COPILOT_PROFILE", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "explicit-profile-test")
    monkeypatch.setenv("CI_ALLOW_SQLITE_FALLBACK", "1")
    assert main._resolve_profile() == "production"
    monkeypatch.setenv("S2P_PROFILE", "test")
    assert main._resolve_profile() == "test"
    monkeypatch.setenv("S2P_PROFILE", "production")
    assert main._resolve_profile() == "production"
    monkeypatch.setenv("S2P_PROFILE", "offline")
    assert main._resolve_profile() == "offline"
    monkeypatch.setenv("S2P_PROFILE", "development")
    assert main._resolve_profile() == "offline"


def test_invalid_profile_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S2P_PROFILE", "typo")
    with pytest.raises(ValueError, match="profile must be"):
        main._resolve_profile()


def test_main_configures_writer_and_has_no_test_detection() -> None:
    assert isinstance(main.app.state.audit_writer, s2p.S2PScoreAuditWriter)
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert "PYTEST_CURRENT_TEST" not in source
    assert "sys.modules" not in source
