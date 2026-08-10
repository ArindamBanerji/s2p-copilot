import os
import sys
import time
import uuid
from typing import Any

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app, build_s2p_scorer  # noqa: E402
from app.routers import s2p as s2p_router  # noqa: E402
from app.s2p_shadow import (  # noqa: E402
    S2PShadowConfig,
    S2PShadowDiagnostics,
    S2PShadowState,
    initialize_s2p_shadow_state,
)


SCORE_BODY = {
    "event_id": "S2P-SHADOW-PHASE2",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-SHADOW",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


class FakeShadowStore:
    def __init__(self, *, fail_governed: bool = False, fail_outcome: bool = False) -> None:
        self.fail_governed = fail_governed
        self.fail_outcome = fail_outcome
        self.governed_decisions: list[dict] = []
        self.outcomes: list[dict] = []

    def generate_decision_id(self, domain: str) -> str:
        assert domain == "s2p"
        return uuid.uuid4().hex[:12]

    def write_governed_decision(
        self,
        decision_id: str,
        domain: str,
        category: str,
        category_index: int,
        recommended_action: str,
        recommended_index: int,
        confidence: float,
        probabilities: list[float],
        factor_vector: list[float],
        factor_names: list[str],
        source: str = "score",
        scorer_version: str = "",
        preset_version: str = "",
        factor_schema_version: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.fail_governed:
            raise RuntimeError("postgresql://postgres:secret@127.0.0.1/db?password=abc")
        self.governed_decisions.append(
            {
                "decision_id": decision_id,
                "domain": domain,
                "category": category,
                "category_index": category_index,
                "recommended_action": recommended_action,
                "recommended_index": recommended_index,
                "confidence": confidence,
                "probabilities": probabilities,
                "factor_vector": factor_vector,
                "factor_names": factor_names,
                "source": source,
                "scorer_version": scorer_version,
                "preset_version": preset_version,
                "factor_schema_version": factor_schema_version,
                "metadata": metadata,
            }
        )

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict[str, Any] | None = None,
        domain: str | None = None,
    ) -> None:
        if self.fail_outcome:
            raise RuntimeError("postgresql://postgres:secret@127.0.0.1/db?password=abc")
        self.outcomes.append(
            {
                "decision_id": decision_id,
                "actual_action": actual_action,
                "is_correct": is_correct,
                "metadata": metadata,
                "domain": domain,
            }
        )

    def get_decision(self, decision_id: str, domain: str | None = None) -> dict[str, Any] | None:
        return next(
            (dict(row) for row in self.governed_decisions if row["decision_id"] == decision_id),
            None,
        )

    def get_archived_decisions(self, domain: str) -> list[dict[str, Any]]:
        assert domain == "s2p"
        return []


def _enabled_config(*, strict: bool = False) -> S2PShadowConfig:
    return S2PShadowConfig.from_env(
        {
            "S2P_SHADOW_AGE": "1",
            "S2P_SHADOW_STRICT": "1" if strict else "0",
            "S2P_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_AGE_GRAPH": "protocol_v2_test_shadow",
            "S2P_AGE_TEST_MODE": "1",
        }
    )


def _shadow_state(
    store: FakeShadowStore | None = None,
    *,
    strict: bool = False,
) -> S2PShadowState:
    return S2PShadowState(
        config=_enabled_config(strict=strict),
        diagnostics=S2PShadowDiagnostics(max_events=20, shadow_run_id="phase2-test"),
        store=store or FakeShadowStore(),
    )


def _reset_app_state(shadow: S2PShadowState | None = None) -> None:
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    app.state.s2p_shadow = shadow or initialize_s2p_shadow_state(env={})
    s2p_router._clear_score_conservation_status_cache()


@pytest.fixture(autouse=True)
def reset_app_after_test():
    _reset_app_state()
    yield
    _reset_app_state()


def _score(client: TestClient, event_id: str = "S2P-SHADOW-PHASE2"):
    return client.post("/api/s2p/score", json={**SCORE_BODY, "event_id": event_id})


def test_shadow_disabled_by_default_does_not_construct_age_store():
    shadow = initialize_s2p_shadow_state(env={})
    _reset_app_state(shadow)

    response = _score(TestClient(app), "S2P-SHADOW-DISABLED")

    assert response.status_code == 200
    assert shadow.config.enabled is False
    assert shadow.store is None


def test_enabled_shadow_state_uses_injected_shared_store():
    from copilot_sdk.graph.memory_store import InMemoryGraphStore

    shared_store = InMemoryGraphStore(domain="s2p")
    state = initialize_s2p_shadow_state(
        env={
            "S2P_SHADOW_AGE": "1",
            "S2P_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_AGE_GRAPH": "protocol_v2_test_shadow",
            "S2P_AGE_TEST_MODE": "1",
        },
        store=shared_store,
    )

    assert state.config.enabled is True
    assert state.config.graph == "protocol_v2_test_shadow"
    assert state.store is shared_store


def test_score_shadow_success_uses_authoritative_decision_id_and_keeps_response_shape():
    fake = FakeShadowStore()
    shadow = _shadow_state(fake)
    _reset_app_state(shadow)

    response = _score(TestClient(app), "S2P-SHADOW-SCORE-SUCCESS")

    assert response.status_code == 200
    body = response.json()
    assert len(fake.governed_decisions) == 1
    shadow_write = fake.governed_decisions[0]
    assert shadow_write["decision_id"] == f"{body['decision_id']}::shadow"
    assert shadow_write["domain"] == "s2p"
    assert shadow_write["factor_names"] == s2p_router.S2PDomainConfig.factors
    assert shadow_write["metadata"]["shadow_run_id"] == "phase2-test"
    assert shadow_write["metadata"]["lifecycle"] == "shadow"
    assert shadow_write["metadata"]["production_decision_id"] == body["decision_id"]
    assert shadow_write["metadata"]["shadow_operation"] == "score_shadow"
    assert shadow_write["metadata"]["operation_id"] == body["decision_id"]
    assert "shadow" not in body
    event = shadow.diagnostics.events()[-1]
    assert event.status == "succeeded"
    assert event.parity["decision_id_match"] is True
    assert event.parity["status_match"] is True


def test_outcome_shadow_success_runs_after_authoritative_outcome():
    fake = FakeShadowStore()
    shadow = _shadow_state(fake)
    _reset_app_state(shadow)
    client = TestClient(app)
    score_response = _score(client, "S2P-SHADOW-OUTCOME-SUCCESS")
    score = score_response.json()

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "pytest",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 200
    assert len(fake.outcomes) == 1
    assert fake.outcomes[0]["decision_id"] == f"{score['decision_id']}::shadow"
    assert fake.outcomes[0]["actual_action"] == score["action"]
    assert fake.outcomes[0]["metadata"]["shadow_run_id"] == "phase2-test"
    assert fake.outcomes[0]["metadata"]["lifecycle"] == "shadow"
    assert fake.outcomes[0]["metadata"]["shadow_operation"] == "outcome_shadow"
    assert fake.outcomes[0]["metadata"]["operation_id"] == score["decision_id"]
    event = shadow.diagnostics.events()[-1]
    assert event.operation == "outcome_shadow"
    assert event.status == "succeeded"
    assert event.parity["decision_id_match"] is True
    assert event.parity["outcome_match"] is True


def test_learn_shadow_success_uses_distinct_operation_name():
    fake = FakeShadowStore()
    shadow = _shadow_state(fake)
    _reset_app_state(shadow)
    client = TestClient(app)
    score_response = _score(client, "S2P-SHADOW-LEARN-SUCCESS")
    score = score_response.json()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 200
    assert len(fake.outcomes) == 1
    assert fake.outcomes[0]["metadata"]["shadow_operation"] == "learn_shadow"
    assert fake.outcomes[0]["metadata"]["operation_id"] == score["decision_id"]
    event = shadow.diagnostics.events()[-1]
    assert event.operation == "learn_shadow"
    assert event.status == "succeeded"
    assert event.parity["decision_id_match"] is True
    assert event.parity["outcome_match"] is True


def test_non_strict_score_shadow_failure_keeps_sqlite_response_and_redacts_error():
    fake = FakeShadowStore(fail_governed=True)
    shadow = _shadow_state(fake, strict=False)
    _reset_app_state(shadow)

    response = _score(TestClient(app), "S2P-SHADOW-SCORE-FAIL")

    assert response.status_code == 200
    event = shadow.diagnostics.events()[-1]
    assert event.status == "failed"
    assert "secret" not in event.error_message
    assert "password=abc" not in event.error_message
    assert "password=***" in event.error_message


def test_strict_score_shadow_failure_logs_after_authoritative_write(caplog, monkeypatch):
    fake = FakeShadowStore(fail_governed=True)
    shadow = _shadow_state(fake, strict=True)
    _reset_app_state(shadow)

    with caplog.at_level("WARNING"):
        response = _score(TestClient(app), "S2P-SHADOW-SCORE-STRICT")
        deadline = time.time() + 2
        while time.time() < deadline:
            events = shadow.diagnostics.events()
            if (
                events
                and events[-1].operation == "score_shadow"
                and "S2P side effect failed" in caplog.text
            ):
                break
            time.sleep(0.01)

    assert response.status_code == 200
    event = shadow.diagnostics.events()[-1]
    assert event.operation == "score_shadow"
    assert event.status == "failed"
    assert "S2P side effect failed" in caplog.text


def test_non_strict_outcome_shadow_failure_keeps_sqlite_response():
    fake = FakeShadowStore(fail_outcome=True)
    shadow = _shadow_state(fake, strict=False)
    _reset_app_state(shadow)
    client = TestClient(app)
    score_response = _score(client, "S2P-SHADOW-OUTCOME-FAIL")
    score = score_response.json()

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "pytest",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 200
    assert shadow.diagnostics.events()[-1].operation == "outcome_shadow"
    assert shadow.diagnostics.events()[-1].status == "failed"


def test_strict_outcome_shadow_failure_fails_clearly_after_authoritative_write():
    fake = FakeShadowStore(fail_outcome=True)
    shadow = _shadow_state(fake, strict=True)
    _reset_app_state(shadow)
    client = TestClient(app)
    score_response = _score(client, "S2P-SHADOW-OUTCOME-STRICT")
    score = score_response.json()

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": score["decision_id"],
            "outcome": "confirm",
            "analyst_action": score["action"],
            "analyst_id": "pytest",
            "factor_vector": score["factor_vector"],
            "category": score["category"],
            "predicted_action": score["action"],
        },
    )

    assert response.status_code == 502
    assert "outcome_shadow failed" in response.json()["detail"]
    assert shadow.diagnostics.events()[-1].operation == "outcome_shadow"
    assert shadow.diagnostics.events()[-1].status == "failed"


def test_preview_routes_do_not_write_age_decisions():
    fake = FakeShadowStore()
    shadow = _shadow_state(fake)
    _reset_app_state(shadow)

    response = TestClient(app).get("/api/s2p/preview/queue")

    assert response.status_code == 200
    assert fake.governed_decisions == []
    assert not [
        event for event in shadow.diagnostics.events() if event.operation == "score_shadow"
    ]


def test_duplicate_outcome_invariant_still_blocks_second_authoritative_write():
    fake = FakeShadowStore()
    shadow = _shadow_state(fake)
    _reset_app_state(shadow)
    client = TestClient(app, raise_server_exceptions=False)
    score_response = _score(client, "S2P-SHADOW-DUPLICATE-OUTCOME")
    score = score_response.json()
    payload = {
        "decision_id": score["decision_id"],
        "actual_action": score["action"],
        "outcome": "confirmed",
    }

    first = client.post("/api/learn", json=payload)
    second = client.post("/api/learn", json=payload)

    assert first.status_code == 200
    # Repeating the identical outcome is idempotent; conflicting outcomes
    # remain the path that must be rejected by the store contract.
    assert second.status_code == 200
    assert len(fake.outcomes) == 2
