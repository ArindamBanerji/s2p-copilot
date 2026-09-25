"""Allocation response mapping and request attribution, without fake policies."""
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import main
from app.domains.s2p.config import S2PDomainConfig
from copilot_sdk.scoring.budget_policy import AdaptiveBudgetPolicy


@pytest.fixture
def policy() -> Iterator[AdaptiveBudgetPolicy]:
    old_state = main.app.state.investigation_budget_policy
    old_classifier = main.SituationClassifier._budget_policy
    real = AdaptiveBudgetPolicy(safety_lambda=0.0)
    main.app.state.investigation_budget_policy = real
    main.SituationClassifier._budget_policy = real
    try:
        yield real
    finally:
        main.app.state.investigation_budget_policy = old_state
        main.SituationClassifier._budget_policy = old_classifier


def test_profiles_reads_and_required_fields(policy: AdaptiveBudgetPolicy) -> None:
    for confidence in (0.95, 0.85, 0.8, 0.65, 0.64):
        policy.allocate(confidence, "price_variance", 50)
    with TestClient(main.app) as client:
        response = client.get("/api/self/investigation-budget")
    assert response.status_code == 200
    rows = response.json()["allocations"]
    assert [row["profile"] for row in rows] == ["easy", "easy", "medium", "medium", "hard"]
    assert [row["reads"] for row in rows] == [1, 1, 2, 2, 4]
    for row in rows:
        assert {"profile", "reads", "confidence", "category", "decision_id"} <= row.keys()
        assert row["category"] == "price_variance"
        assert row["decision_id"] is None  # Direct legacy allocations have no request.


def test_empty_history_does_not_invent_allocations(policy: AdaptiveBudgetPolicy) -> None:
    with TestClient(main.app) as client:
        assert client.get("/api/self/investigation-budget").json()["allocations"] == []


def test_full_bounded_history_retains_easy_example(policy: AdaptiveBudgetPolicy) -> None:
    policy.allocate(0.95, "price_variance", 50)
    for _ in range(15):
        policy.allocate(0.2, "contract_gap", 50)
    with TestClient(main.app) as client:
        body = client.get("/api/self/investigation-budget").json()
    assert body["allocations"][0]["profile"] == "easy"
    assert len(body["recent_allocations"]) == 10


def test_concurrent_investigations_keep_decision_and_category_attribution(
    policy: AdaptiveBudgetPolicy,
) -> None:
    store = main.app.state.graph_store
    for _ in range(30):
        decision = store.write_decision(
            domain="s2p", category="price_variance", action="auto_approve",
            confidence=0.9, factors={},
        )
        store.write_outcome(decision, "auto_approve", True, domain="s2p")
    inputs: list[dict[str, Any]] = []
    for category in ("price_variance", "contract_gap"):
        decision = store.write_decision(
            domain="s2p", category=category, action="auto_approve",
            confidence=0.9, factors={},
        )
        inputs.append({"decision_id": decision, "category": category,
                       "factor_vector": [0.5] * S2PDomainConfig.n_factors, "use_K": False})
    with TestClient(main.app) as client:
        def investigate(body: dict[str, Any]) -> None:
            response = client.post("/api/investigation/investigate", json=body)
            assert response.status_code == 200, response.text
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(investigate, inputs))
        rows = client.get("/api/self/investigation-budget").json()["allocations"]
    assert {(r["decision_id"], r["category"]) for r in rows} == {
        (body["decision_id"], body["category"]) for body in inputs
    }
    assert main._budget_request.get() is None
    for row in rows:
        expected = "easy" if row["confidence"] >= 0.85 else "medium" if row["confidence"] >= 0.65 else "hard"
        assert row["profile"] == expected


def test_cold_start_does_not_relabel_an_old_allocation(policy: AdaptiveBudgetPolicy) -> None:
    policy.allocate(0.9, "price_variance", 30)
    main.SituationClassifier().classify(
        [0.1] * 8, [[0.1] * 8, [0.9] * 8], [0.2] * 8,
        [0.91, 0.09], [0.5, 0.5],
    )
    assert len(policy._history) == 1
    assert policy._history[0]["category"] == "price_variance"
    assert "decision_id" not in policy._history[0]
