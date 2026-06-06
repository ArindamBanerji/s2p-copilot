import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402
from app.s2p_graph_status import (  # noqa: E402
    S2PActiveAGEGraphStore,
    S2PActiveGraphConfig,
    create_s2p_active_graph_store,
)
from app.s2p_shadow import initialize_s2p_shadow_state  # noqa: E402


PARALLEL_ENV_KEYS = (
    "S2P_ACTIVE_PARALLEL_AGE_TEST",
    "S2P_ACTIVE_GRAPH_BACKEND",
    "S2P_ACTIVE_AGE_DSN",
    "S2P_ACTIVE_AGE_GRAPH",
    "S2P_ACTIVE_AGE_DOMAIN",
    "S2P_ACTIVE_AGE_TEST_MODE",
)

BASE_SCORE_BODY = {
    "category": "price_variance",
    "amount": 5000.0,
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def _parallel_age_env_present() -> bool:
    if os.environ.get("S2P_ACTIVE_PARALLEL_AGE_TEST") != "1":
        return False
    return all(os.environ.get(key) for key in PARALLEL_ENV_KEYS[1:])


pytestmark = pytest.mark.skipif(
    not _parallel_age_env_present(),
    reason="set S2P_ACTIVE_PARALLEL_AGE_TEST=1 and S2P active AGE env vars",
)


def _reset_app_with_parallel_active_age() -> S2PActiveAGEGraphStore:
    config = S2PActiveGraphConfig.from_env()
    assert config.requested_backend == "age"
    assert config.domain == "s2p"
    assert config.graph != "soc_graph"
    assert str(config.graph).startswith("protocol_v2_test")
    assert config.test_mode is True
    active_store = create_s2p_active_graph_store(config)
    assert isinstance(active_store, S2PActiveAGEGraphStore)
    app.state.s2p_active_graph_config = config
    app.state.scorer = build_s2p_scorer(graph_store=active_store)
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    app.state.s2p_shadow = initialize_s2p_shadow_state(env={})
    s2p_router._clear_score_conservation_status_cache()
    return active_store


def _score_payload(index: int) -> dict:
    unique = uuid4()
    return {
        **BASE_SCORE_BODY,
        "event_id": f"S2P-ACTIVE-PARALLEL-{index}-{unique}",
        "supplier_id": f"SUP-ACTIVE-PARALLEL-{index}-{unique}",
    }


def _run_parallel_flow(index: int, mode: str) -> dict:
    client = TestClient(app)
    score_response = client.post("/api/s2p/score", json=_score_payload(index))
    assert score_response.status_code == 200, score_response.text
    score = score_response.json()

    if mode == "outcome":
        outcome_response = client.post(
            "/api/s2p/outcome",
            json={
                "decision_id": score["decision_id"],
                "outcome": "confirm",
                "analyst_action": score["action"],
                "analyst_id": f"pytest-parallel-{index}",
                "factor_vector": score["factor_vector"],
                "category": score["category"],
                "predicted_action": score["action"],
            },
        )
    else:
        outcome_response = client.post(
            "/api/learn",
            json={
                "decision_id": score["decision_id"],
                "actual_action": score["action"],
                "outcome": "confirmed",
            },
        )
    assert outcome_response.status_code == 200, outcome_response.text
    return {
        "decision_id": score["decision_id"],
        "action": score["action"],
        "category": score["category"],
        "factor_vector": score["factor_vector"],
        "mode": mode,
    }


def test_parallel_active_age_score_outcome_and_learn_gate():
    active_store = _reset_app_with_parallel_active_age()
    client = TestClient(app)
    before_preview_count = active_store.count_decisions("s2p")

    preview_response = client.get("/api/s2p/preview/queue")
    assert preview_response.status_code == 200
    assert active_store.count_decisions("s2p") == before_preview_count

    modes = ["outcome"] * 4 + ["learn"] * 4
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(_run_parallel_flow, index, mode)
            for index, mode in enumerate(modes)
        ]
        for future in as_completed(futures):
            results.append(future.result())

    assert len(results) == 8
    decision_ids = {result["decision_id"] for result in results}
    assert len(decision_ids) == 8

    verified = active_store.get_verified_decisions("s2p")
    verified_by_id = {item.get("decision_id"): item for item in verified}
    for result in results:
        decision = active_store.get_decision(result["decision_id"])
        assert decision is not None
        assert decision["decision_id"] == result["decision_id"]
        assert decision["domain"] == "s2p"
        assert decision["status"] in {"confirmed", "overridden"}
        assert result["decision_id"] in verified_by_id

    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "age"
    assert status["age_active"] is True
    assert status["active_test_mode"] is True
    assert status["active_graph_name"] != "soc_graph"
    assert "postgres:postgres@" not in str(status)

    duplicate_target = results[0]
    duplicate_client = TestClient(app, raise_server_exceptions=False)
    if duplicate_target["mode"] == "outcome":
        duplicate = duplicate_client.post(
            "/api/s2p/outcome",
            json={
                "decision_id": duplicate_target["decision_id"],
                "outcome": "confirm",
                "analyst_action": duplicate_target["action"],
                "analyst_id": "pytest-parallel-duplicate",
                "factor_vector": duplicate_target["factor_vector"],
                "category": duplicate_target["category"],
                "predicted_action": duplicate_target["action"],
            },
        )
    else:
        duplicate = duplicate_client.post(
            "/api/learn",
            json={
                "decision_id": duplicate_target["decision_id"],
                "actual_action": duplicate_target["action"],
                "outcome": "confirmed",
            },
        )
    assert duplicate.status_code != 200
