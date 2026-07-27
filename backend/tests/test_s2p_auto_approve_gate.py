from __future__ import annotations

from fastapi.testclient import TestClient
import pytest
from types import SimpleNamespace

from app.graph.s2p_graph_reader import S2PGraphReader
from app.main import app, build_s2p_scorer
from app.routers import s2p_auto_approve as p40_router
from app.services.novelty_tracker import reset_novelty_tracker
from app.services.s2p_auto_approve_gate import AutoApproveConfig, AutoApproveGate


client = TestClient(app)


def _verified_rows(category: str = "price_variance", count: int = 100, correct: int | None = None):
    correct = count if correct is None else correct
    rows = []
    for index in range(count):
        rows.append(
            {
                "decision_id": f"D-{category}-{index}",
                "category": category,
                "recommended_action": "auto_approve",
                "is_correct": index < correct,
                "verified_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00Z",
            }
        )
    return rows


class FakeGraphStore:
    domain = "s2p"

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.write_outcome_calls = 0

    def get_verified_decisions(self, domain: str | None = None):
        if domain is not None:
            assert domain == "s2p"
        return list(self.rows)

    def count_verified(self, domain):
        assert domain == "s2p"
        return len(self.rows)

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict | None = None,
        domain: str | None = None,
    ) -> None:
        self.write_outcome_calls += 1
        raise AssertionError("shadow gate must not write outcomes")

    def get_decision(self, decision_id: str, domain: str | None = None):
        return None

    def get_archived_decisions(self, domain: str):
        return []


class SpyScorer:
    def __init__(self, graph_store):
        self.graph_store = graph_store
        self.learn_calls = 0

    def learn(self, *args, **kwargs):
        self.learn_calls += 1
        raise AssertionError("shadow gate must not call learn")


def _set_graph_store(store):
    app.state.graph_store = store
    app.state.scorer = SimpleNamespace(graph_store=store)
    app.state.s2p_graph_reader = S2PGraphReader(store=store)


@pytest.fixture(autouse=True)
def reset_gate(monkeypatch):
    monkeypatch.setattr(p40_router, "gate", AutoApproveGate())
    reset_novelty_tracker()
    scorer = build_s2p_scorer()
    app.state.scorer = scorer
    app.state.graph_store = scorer.graph_store
    monkeypatch.setattr(p40_router, "_current_conservation_status", lambda _request: "GREEN")
    yield
    reset_novelty_tracker()


def test_auto_approve_shadow_disabled_by_default():
    gate = AutoApproveGate()

    assert gate.config.enabled is False
    assert gate.config.mode == "disabled"


def test_enable_accepts_shadow_only():
    response = client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow"})

    assert response.status_code == 200
    assert response.json()["mode"] == "shadow"
    assert response.json()["execution_authority"] is False


def test_enable_rejects_execute_pending_verification():
    response = client.post(
        "/api/s2p/auto-approve/enable",
        json={"mode": "execute_pending_verification"},
    )

    assert response.status_code == 400
    assert "shadow mode only" in response.json()["detail"]


def test_enable_rejects_assistive_mode():
    response = client.post(
        "/api/s2p/auto-approve/enable",
        json={"mode": "assistive"},
    )

    assert response.status_code == 400
    assert "shadow mode only" in response.json()["detail"]


def test_configure_updates_existing_category_thresholds():
    gate = AutoApproveGate(AutoApproveConfig(initial_threshold=0.95))
    store = FakeGraphStore(_verified_rows())
    before = gate.status_by_category(
        graph_store=store,
        conservation_status="GREEN",
    )
    assert before["category_states"]["price_variance"]["threshold"] == pytest.approx(0.95)

    gate.configure(enabled=True, mode="shadow", initial_threshold=0.99, min_verified_decisions=1)
    after = gate.status_by_category(
        graph_store=store,
        conservation_status="GREEN",
    )
    assert after["category_states"]["price_variance"]["threshold"] == pytest.approx(0.99)

    result = gate.evaluate(
        category="price_variance",
        confidence=0.96,
        recommended_action="auto_approve",
        graph_store=store,
        conservation_status="GREEN",
    )
    assert result["blocked_reason"] == "below_threshold"
    assert result["readiness"]["threshold"] == pytest.approx(0.99)


def test_configure_future_categories_use_new_initial_threshold():
    gate = AutoApproveGate(AutoApproveConfig(initial_threshold=0.95))
    gate.configure(enabled=True, mode="shadow", initial_threshold=0.99, min_verified_decisions=1)
    result = gate.evaluate(
        category="duplicate_risk",
        confidence=0.96,
        recommended_action="auto_approve",
        graph_store=FakeGraphStore(_verified_rows("duplicate_risk", count=10)),
        conservation_status="GREEN",
    )

    assert result["blocked_reason"] == "below_threshold"
    assert result["readiness"]["threshold"] == pytest.approx(0.99)


def test_enable_endpoint_threshold_update_reflected_in_evaluate():
    _set_graph_store(FakeGraphStore(_verified_rows()))
    response = client.post(
        "/api/s2p/auto-approve/enable",
        json={
            "mode": "shadow",
            "initial_threshold": 0.99,
            "min_verified_decisions": 1,
        },
    )
    assert response.status_code == 200

    status = client.get("/api/s2p/auto-approve/status").json()
    assert status["category_states"]["price_variance"]["threshold"] == pytest.approx(0.99)

    evaluated = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.96,
            "recommended_action": "auto_approve",
        },
    ).json()
    assert evaluated["blocked_reason"] == "below_threshold"
    assert evaluated["readiness"]["threshold"] == pytest.approx(0.99)


def test_disabled_does_not_change_score_pipeline():
    before = app.state.graph_store.count_verified("s2p")

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.status_code == 200
    assert response.json()["blocked_reason"] == "disabled"
    assert app.state.graph_store.count_verified("s2p") == before


def test_shadow_evaluate_does_not_call_learn():
    store = FakeGraphStore(_verified_rows())
    scorer = SpyScorer(store)
    _set_graph_store(store)
    app.state.scorer = scorer
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.status_code == 200
    assert scorer.learn_calls == 0


def test_shadow_evaluate_does_not_call_write_outcome():
    store = FakeGraphStore(_verified_rows())
    _set_graph_store(store)
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.status_code == 200
    assert store.write_outcome_calls == 0
    assert response.json()["outcome_written"] is False


def test_shadow_evaluate_does_not_increment_verified_count():
    store = FakeGraphStore(_verified_rows(count=5))
    _set_graph_store(store)
    before = store.count_verified("s2p")
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert store.count_verified("s2p") == before


@pytest.mark.parametrize("status", ["RED", "AMBER"])
def test_conservation_red_blocks(monkeypatch, status):
    monkeypatch.setattr(p40_router, "_current_conservation_status", lambda _request: status)
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.json()["would_auto_approve"] is False
    assert response.json()["blocked_reason"] == "conservation_not_green"


def test_conservation_amber_blocks(monkeypatch):
    monkeypatch.setattr(p40_router, "_current_conservation_status", lambda _request: "AMBER")
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.json()["blocked_reason"] == "conservation_not_green"


def test_conservation_green_required(monkeypatch):
    monkeypatch.setattr(p40_router, "_current_conservation_status", lambda _request: "GREEN")
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post(
        "/api/s2p/auto-approve/enable",
        json={
            "mode": "shadow",
            "initial_threshold": 0.95,
            "min_verified_decisions": 1,
            "spot_check_rate": 0.0,
        },
    )

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.json()["would_auto_approve"] is True


def test_insufficient_category_verified_count_blocks():
    _set_graph_store(FakeGraphStore(_verified_rows(count=2)))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 3})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.json()["blocked_reason"] == "insufficient_category_verified_count"


def test_category_readiness_derived_from_filtered_verified_outcomes():
    rows = _verified_rows("price_variance", count=3) + _verified_rows("duplicate_risk", count=25)
    _set_graph_store(FakeGraphStore(rows))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 10})

    status = client.get("/api/s2p/auto-approve/status").json()

    assert status["category_states"]["price_variance"]["verified_count"] == 3
    assert status["category_states"]["price_variance"]["derived_category_readiness"] == "blocked"
    assert status["category_states"]["duplicate_risk"]["verified_count"] == 25
    assert status["category_states"]["duplicate_risk"]["derived_category_readiness"] == "ready"


def test_global_expansion_counts_not_used_as_category_readiness():
    rows = _verified_rows("duplicate_risk", count=200)
    _set_graph_store(FakeGraphStore(rows))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 10})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.json()["blocked_reason"] == "insufficient_category_verified_count"
    assert response.json()["readiness"]["verified_count"] == 0


def test_confidence_below_threshold_blocks():
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.50,
            "recommended_action": "auto_approve",
        },
    )

    assert response.json()["blocked_reason"] == "below_threshold"


def test_wrong_action_blocks():
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "hold_for_review",
        },
    )

    assert response.json()["blocked_reason"] == "wrong_action"


def test_spot_check_blocks_execution_and_requires_human_review():
    gate = AutoApproveGate(AutoApproveConfig(enabled=True, mode="shadow", spot_check_rate=1.0, min_verified_decisions=1))
    result = gate.evaluate(
        category="price_variance",
        confidence=0.99,
        recommended_action="auto_approve",
        graph_store=FakeGraphStore(_verified_rows()),
        conservation_status="GREEN",
    )

    assert result["would_auto_approve"] is False
    assert result["event"]["status"] == "spot_check_required"
    assert result["event"]["outcome_written"] is False


def test_threshold_expansion_uses_verified_outcomes_only():
    gate = AutoApproveGate(AutoApproveConfig(enabled=True, mode="shadow", min_verified_decisions=5))
    result = gate.maybe_expand_threshold_from_verified_outcomes_only(
        category="price_variance",
        verified_rows=_verified_rows(count=5, correct=5),
        conservation_status="GREEN",
    )

    assert result["changed"] is True
    assert result["reason"] == "expanded_from_verified_outcomes"


def test_threshold_does_not_expand_from_unverified_graphstore_rows():
    gate = AutoApproveGate(AutoApproveConfig(enabled=True, mode="shadow", min_verified_decisions=5))
    result = gate.maybe_expand_threshold_from_verified_outcomes_only(
        category="price_variance",
        verified_rows=[],
        conservation_status="GREEN",
    )

    assert result["changed"] is False
    assert result["reason"] == "insufficient_verified_outcomes"


def test_threshold_cannot_decrease_without_green():
    gate = AutoApproveGate(AutoApproveConfig(enabled=True, mode="shadow", min_verified_decisions=1))
    result = gate.maybe_expand_threshold_from_verified_outcomes_only(
        category="price_variance",
        verified_rows=_verified_rows(count=5, correct=5),
        conservation_status="AMBER",
    )

    assert result["changed"] is False
    assert result["reason"] == "conservation_not_green"


def test_threshold_contracts_after_verified_incorrect_auto_approval():
    gate = AutoApproveGate(AutoApproveConfig(enabled=True, mode="shadow", min_verified_decisions=5))
    result = gate.maybe_expand_threshold_from_verified_outcomes_only(
        category="price_variance",
        verified_rows=_verified_rows(count=5, correct=2),
        conservation_status="GREEN",
    )

    assert result["changed"] is True
    assert result["reason"] == "contracted_after_verified_incorrect_auto_approval"


def test_audit_event_status_shadow_only_not_verified():
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})
    client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
            "decision_id": "S2P-DECISION-1",
        },
    )

    audit = client.get("/api/s2p/auto-approve/audit").json()
    event = audit["shadow_evaluation_log"][-1]
    assert event["status"] == "shadow_only"
    assert event["verified"] is False
    assert event["source"] == "auto_approve_shadow"


def test_audit_event_marks_learning_applied_false():
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})
    client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    event = client.get("/api/s2p/auto-approve/audit").json()["shadow_evaluation_log"][-1]
    assert event["learning_applied"] is False
    assert event["outcome_written"] is False


def test_shadow_approval_does_not_create_pending_verification_count():
    _set_graph_store(FakeGraphStore(_verified_rows()))
    client.post("/api/s2p/auto-approve/enable", json={"mode": "shadow", "min_verified_decisions": 1})

    response = client.post(
        "/api/s2p/auto-approve/evaluate",
        json={
            "category": "price_variance",
            "confidence": 0.99,
            "recommended_action": "auto_approve",
        },
    )

    assert response.json()["would_auto_approve"] is True
    status = client.get("/api/s2p/auto-approve/status").json()
    state = status["category_states"]["price_variance"]
    assert state["auto_approved_count"] == 1
    assert state["pending_verification_count"] == 0


def test_p39_context_metrics_do_not_gate_automation():
    gate = AutoApproveGate(AutoApproveConfig(enabled=True, mode="shadow", min_verified_decisions=1))
    result = gate.evaluate(
        category="price_variance",
        confidence=0.99,
        recommended_action="auto_approve",
        graph_store=FakeGraphStore([]),
        conservation_status="GREEN",
        p39_evidence={"exception_rate": {"source": "fixture", "provenance_tier": "context"}},
    )

    assert result["blocked_reason"] == "insufficient_category_verified_count"
    assert result["p39_evidence"]["exception_rate"]["source"] == "fixture"


def test_p39_verified_metrics_reported_as_evidence_only():
    gate = AutoApproveGate(
        AutoApproveConfig(enabled=True, mode="shadow", min_verified_decisions=1, spot_check_rate=0.0)
    )
    evidence = {"exception_rate": {"source": "verified_outcomes", "provenance_tier": "learned"}}
    result = gate.evaluate(
        category="price_variance",
        confidence=0.99,
        recommended_action="auto_approve",
        graph_store=FakeGraphStore(_verified_rows(count=1)),
        conservation_status="GREEN",
        p39_evidence=evidence,
    )

    assert result["would_auto_approve"] is True
    assert result["p39_evidence"] == evidence


def test_auto_approve_blocked_when_novelty_active():
    tracker = reset_novelty_tracker()
    for index in range(10):
        tracker.record([float(index)] * 7, "price_variance", 0.8 if index < 3 else 0.1)
    gate = AutoApproveGate(AutoApproveConfig(enabled=True, mode="shadow", min_verified_decisions=1))

    result = gate.evaluate(
        category="price_variance",
        confidence=0.99,
        recommended_action="auto_approve",
        graph_store=FakeGraphStore(_verified_rows(count=10)),
        conservation_status="GREEN",
    )

    assert result["would_auto_approve"] is False
    assert result["blocked_reason"] == "novelty_spike"


def test_existing_score_auto_approve_response_preserved():
    response = client.post(
        "/api/s2p/score",
        json={
            "event_id": "E001",
            "category": "price_variance",
            "amount": 5000.0,
            "supplier_id": "SUP-001",
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
    assert "auto_approve" in response.json()


def test_existing_stats_route_preserved():
    response = client.get("/api/s2p/auto-approve/stats")

    assert response.status_code == 200
    assert response.json()["source"] == "in_memory_demo_stats"


def test_existing_expansion_proof_route_preserved():
    response = client.get("/api/s2p/auto-approve/expansion-proof?category=price_variance")

    assert response.status_code == 200
    assert "safe_to_expand" in response.json()
