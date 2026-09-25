"""Budget restart contracts through real policies, scorers and graph stores."""
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app import main
from app.domains.s2p.config import S2PDomainConfig
from app.evidence_provider import S2PEvidenceProvider
from app.routers.s2p_demo_control import budget_candidates
from app.services.budget_persistence import PersistentBudgetPolicy, STATE_KEY
from copilot_sdk.backend.investigation_router import create_investigation_router
from copilot_sdk.graph.memory_store import InMemoryGraphStore
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.graph.sqlite_store import SQLiteGraphStore
from copilot_sdk.scoring import CompoundingScorer
from copilot_sdk.scoring.budget_policy import MAX_HISTORY


@pytest.fixture(params=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[GraphStore]:
    real: GraphStore = (InMemoryGraphStore(domain="s2p") if request.param == "memory"
            else SQLiteGraphStore(tmp_path / "budget.db", domain="s2p"))
    try:
        yield real
    finally:
        real.close()


def scorer_with_outcomes(store: GraphStore) -> CompoundingScorer:
    scorer = CompoundingScorer.from_preset("s2p", graph_store=store, profile="test")
    for index in range(30):
        decision = scorer.score(
            {name: 0.5 for name in S2PDomainConfig.factors},
            S2PDomainConfig.categories[index % S2PDomainConfig.n_categories],
            metadata={"planted": True, "provenance": "sample"},
        )
        store.write_outcome(decision.decision_id, decision.action, True, domain="s2p")
    assert scorer.get_verified_count() == 30
    return scorer


def budget_app(policy: PersistentBudgetPolicy, scorer: CompoundingScorer) -> FastAPI:
    # Scope the actual classifier through subclass configuration, no patching.
    class Classifier(main.SituationClassifier):
        _budget_policy = policy
        _verified_count_provider = staticmethod(scorer.get_verified_count)

    app = FastAPI()
    app.state.investigation_budget_policy = policy
    app.add_api_route("/api/self/investigation-budget", main.s2p_investigation_budget)
    app.include_router(create_investigation_router(
        scorer_provider=lambda: scorer,
        evidence_provider_factory=lambda decision_id: S2PEvidenceProvider({}, decision_id),
        classifier=Classifier(), factor_names=list(S2PDomainConfig.factors),
    ), dependencies=[Depends(main.investigation_budget_context)])
    return app


def test_real_investigations_survive_sqlite_reopen(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    first = SQLiteGraphStore(path, domain="s2p")
    try:
        scorer = scorer_with_outcomes(first)
        policy = PersistentBudgetPolicy(first)
        candidates = budget_candidates(scorer, "contract_gap")
        with TestClient(budget_app(policy, scorer)) as client:
            ids = []
            for profile in ("easy", "hard"):
                factors = candidates[profile]["factors"]
                decision = scorer.score(factors, "contract_gap", metadata={
                    "planted": True, "provenance": "sample",
                })
                ids.append(decision.decision_id)
                response = client.post("/api/investigation/investigate", json={
                    "decision_id": decision.decision_id, "category": "contract_gap",
                    "factor_vector": [factors[name] for name in S2PDomainConfig.factors],
                    "use_K": False,
                })
                assert response.status_code == 200, response.text
            before = client.get("/api/self/investigation-budget").json()
        rows = before["allocations"]
        assert [row["reads"] for row in rows] == [1, 4]
        assert [row["decision_id"] for row in rows] == ids
        assert [row["profile"] for row in rows] == ["easy", "hard"]
        for row in rows:
            assert row["allocation_id"] and row["recorded_at"]
            assert row["verified_count"] == 30
            assert row["provenance"]["measurement"] == "allocated_reads"
            assert row["provenance"]["decision_found"] is True
            assert row["provenance"]["metadata"]["planted"] is True
            assert row["provenance"]["metadata"]["provenance"] == "sample"
        persisted = first.list_governance("s2p")
    finally:
        first.close()

    reopened = SQLiteGraphStore(path, domain="s2p")
    try:
        # A different runtime's centroids must not reclassify stored evidence.
        new_scorer = CompoundingScorer.from_preset("s2p", graph_store=reopened, profile="test")
        reopened.connection.execute("PRAGMA query_only=ON")
        restored = PersistentBudgetPolicy(reopened)
        with TestClient(budget_app(restored, new_scorer)) as client:
            assert client.get("/api/self/investigation-budget").json() == before
            assert client.get("/api/self/investigation-budget").json() == before
        assert reopened.list_governance("s2p") == persisted
        assert PersistentBudgetPolicy(reopened).get_stats() == restored.get_stats()
        reopened.connection.execute("PRAGMA query_only=OFF")
        restored.allocate(0.7, "contract_gap", 30, decision_id=ids[0])
        continued = PersistentBudgetPolicy(reopened)
        assert continued.get_stats()["decisions"] == 3
        assert continued._history[:2] == policy._history
    finally:
        reopened.close()


def test_history_bound_preserves_exact_lifetime_counters(store: GraphStore) -> None:
    policy = PersistentBudgetPolicy(store)
    for index in range(MAX_HISTORY + 11):
        policy.allocate((0.95, 0.7, 0.3)[index % 3], "contract_gap", 30)
    before = deepcopy(policy._history)
    stats = policy.get_stats()
    persisted = store.get_governance("s2p", STATE_KEY)
    assert persisted is not None and len(persisted["history"]) == MAX_HISTORY
    restored = PersistentBudgetPolicy(store)
    assert restored._history == before
    assert restored.get_stats() == stats
    assert stats["decisions"] == MAX_HISTORY + 11
    assert len({row["allocation_id"] for row in before}) == MAX_HISTORY
    assert all(row["decision_id"] is None for row in before)
    restored.allocate(0.95, "price_variance", 30)
    assert len(restored._history) == MAX_HISTORY
    assert restored.get_stats()["decisions"] == MAX_HISTORY + 12
    assert PersistentBudgetPolicy(store).get_stats() == restored.get_stats()


def test_verified_history_does_not_invent_allocations(store: GraphStore) -> None:
    scorer = scorer_with_outcomes(store)
    policy = PersistentBudgetPolicy(store)
    with TestClient(budget_app(policy, scorer)) as client:
        body = client.get("/api/self/investigation-budget").json()
    assert body["allocations"] == [] and body["warm"] is False
    assert body["decisions"] == 0
    assert store.get_governance("s2p", STATE_KEY) is None
    assert policy.allocate(0.9, "contract_gap", 29) == 2
    assert store.get_governance("s2p", STATE_KEY) is None
    policy.allocate(0.9, "contract_gap", 30)
    before = store.get_governance("s2p", STATE_KEY)
    policy.allocate(0.2, "price_variance", 0, decision_id="unobserved")
    assert store.get_governance("s2p", STATE_KEY) == before


def test_concurrent_request_provenance_is_persisted(store: GraphStore) -> None:
    scorer = scorer_with_outcomes(store)
    policy = PersistentBudgetPolicy(store)
    inputs: list[dict[str, Any]] = []
    for category in S2PDomainConfig.categories:
        factors = {name: 0.5 for name in S2PDomainConfig.factors}
        decision = scorer.score(factors, category, metadata={"planted": True})
        inputs.append({"decision_id": decision.decision_id, "category": category,
                       "factor_vector": list(factors.values()), "use_K": False})
    with TestClient(budget_app(policy, scorer)) as client:
        def investigate(body: dict[str, Any]) -> None:
            response = client.post("/api/investigation/investigate", json=body)
            assert response.status_code == 200, response.text
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(investigate, inputs))
    restored = PersistentBudgetPolicy(store)
    assert {(row["decision_id"], row["category"]) for row in restored._history} == {
        (body["decision_id"], body["category"]) for body in inputs
    }
    assert restored.get_stats()["decisions"] == len(inputs)
    assert main._budget_request.get() is None


def test_storage_failure_rolls_back_telemetry(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / "failure.db", domain="s2p")
    try:
        policy = PersistentBudgetPolicy(store)
        policy.allocate(0.95, "contract_gap", 30)
        before = deepcopy(policy._history), policy.get_stats()
        store.connection.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError):
            policy.allocate(0.2, "contract_gap", 30)
        assert (policy._history, policy.get_stats()) == before
        restored = PersistentBudgetPolicy(store)
        assert (restored._history, restored.get_stats()) == before
    finally:
        store.close()


def test_http_storage_failure_does_not_report_success(tmp_path: Path) -> None:
    store = SQLiteGraphStore(tmp_path / "http-failure.db", domain="s2p")
    try:
        scorer = scorer_with_outcomes(store)
        factors = {name: 0.5 for name in S2PDomainConfig.factors}
        decision = scorer.score(factors, "contract_gap")
        policy = PersistentBudgetPolicy(store)
        store.connection.execute("PRAGMA query_only=ON")
        with TestClient(budget_app(policy, scorer)) as client:
            response = client.post("/api/investigation/investigate", json={
                "decision_id": decision.decision_id, "category": "contract_gap",
                "factor_vector": list(factors.values()), "use_K": False,
            })
            assert response.status_code == 503, response.text
            assert client.get("/api/self/investigation-budget").json()["allocations"] == []
        assert store.get_governance("s2p", STATE_KEY) is None
    finally:
        store.close()


def test_explicit_budget_is_not_attributed_to_adaptive_policy(store: GraphStore) -> None:
    scorer = scorer_with_outcomes(store)
    factors = {name: 0.5 for name in S2PDomainConfig.factors}
    decision = scorer.score(factors, "contract_gap")
    policy = PersistentBudgetPolicy(store)
    with TestClient(budget_app(policy, scorer)) as client:
        response = client.post("/api/investigation/investigate", json={
            "decision_id": decision.decision_id, "category": "contract_gap",
            "factor_vector": list(factors.values()), "use_K": False, "budget": 3,
        })
        assert response.status_code == 200, response.text
        assert client.get("/api/self/investigation-budget").json()["allocations"] == []
    assert store.get_governance("s2p", STATE_KEY) is None


@pytest.mark.parametrize("damage", [
    "version", "counters", "confidence", "provenance", "reads_above_max",
    "easy_count", "easy_reads", "hard_reads", "reads_saved", "visible_counts",
    "history_length",
])
def test_malformed_snapshot_fails_explicitly(store: GraphStore, damage: str) -> None:
    policy = PersistentBudgetPolicy(store)
    policy.allocate(0.9, "contract_gap", 30)
    state = store.get_governance("s2p", STATE_KEY)
    assert state is not None
    if damage == "version":
        state["version"] = 99
    elif damage == "counters":
        state["counters"]["_total_allocations"] = 0
    elif damage == "reads_above_max":
        state["history"][0]["reads_allocated"] = policy.max_reads + 1
    elif damage == "easy_count":
        state["counters"]["_total_easy"] = 2
    elif damage == "easy_reads":
        state["counters"]["_total_easy_reads"] = 2
    elif damage == "hard_reads":
        state["counters"]["_total_hard_reads"] = 4
    elif damage == "reads_saved":
        state["counters"]["_total_reads_saved"] = 999
    elif damage == "visible_counts":
        state["counters"].update({"_total_easy": 0, "_total_easy_reads": 0, "_total_reads_saved": 0})
    elif damage == "history_length":
        state["history"] = []
    else:
        state["history"][0][damage] = None
    store.save_governance("s2p", STATE_KEY, state)
    with pytest.raises(ValueError):
        PersistentBudgetPolicy(store)


def test_other_domain_snapshot_is_not_restored(store: GraphStore) -> None:
    store.save_governance("soc", STATE_KEY, {"version": "unrelated"})
    assert PersistentBudgetPolicy(store)._history == []


def test_interrupted_slot_replacement_preserves_latest_snapshot(store: GraphStore) -> None:
    policy = PersistentBudgetPolicy(store)
    policy.allocate(0.9, "contract_gap", 30)
    policy.allocate(0.2, "contract_gap", 30)
    # AGE's delete/create replacement can stop after deleting the older slot.
    # Exercise that actual persisted state with both real store implementations.
    for next_slot in (STATE_KEY, STATE_KEY + ":alternate"):
        history, stats = deepcopy(policy._history), policy.get_stats()
        store.delete_governance("s2p", next_slot)
        policy = PersistentBudgetPolicy(store)
        assert policy._history == history
        assert policy.get_stats() == stats
        policy.allocate(0.7, "contract_gap", 30)
        assert PersistentBudgetPolicy(store).get_stats() == policy.get_stats()
    assert len(store.list_governance("s2p")) == 2


def test_concurrent_telemetry_snapshots_are_consistent_and_detached(store: GraphStore) -> None:
    policy = PersistentBudgetPolicy(store)

    def allocate_and_read(worker: int) -> None:
        for _ in range(20):
            policy.allocate(0.95 if worker % 2 else 0.2, "contract_gap", 30)
            stats, rows = policy.telemetry_snapshot()
            assert stats["decisions"] == len(rows)
            assert stats["easy_count"] == sum(row["reads_allocated"] == 1 for row in rows)
            assert stats["hard_count"] == sum(row["reads_allocated"] == 4 for row in rows)
            assert stats["total_reads_saved"] == sum(2 - row["reads_allocated"] for row in rows)
            separate_stats = policy.get_stats()
            assert separate_stats["decisions"] == separate_stats["easy_count"] + separate_stats["hard_count"]
            rows[-1]["provenance"]["measurement"] = "caller mutation"

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(allocate_and_read, range(4)))
    stats, rows = policy.telemetry_snapshot()
    assert stats["decisions"] == 80
    assert all(row["provenance"]["measurement"] == "allocated_reads" for row in rows)
    assert PersistentBudgetPolicy(store).telemetry_snapshot() == (stats, rows)
