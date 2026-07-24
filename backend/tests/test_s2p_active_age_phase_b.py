import os
import sys
import hashlib
import json
import uuid
from typing import Any

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


VALID_SCORE_REQUEST = {
    "event_id": "S2P-CUTOVER-PHASE-B",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-CUTOVER-B",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


class FakeAGEStore:
    domain = "s2p"

    def __init__(self) -> None:
        self.decisions: dict[str, dict[str, Any]] = {}
        self.links: list[dict[str, str]] = []
        self.evidence_receipts: list[dict[str, Any]] = []
        self.outbox: list[dict[str, Any]] = []
        self.governed_writes = 0
        self.outcome_writes = 0
        self.fail_evidence_receipt = False
        self.fail_outbox = False

    def generate_decision_id(self, domain: str) -> str:
        assert domain == self.domain
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
        assert domain == self.domain
        metadata = dict(metadata or {})
        if decision_id in self.decisions:
            raise ValueError(f"duplicate governed decision_id in domain: {decision_id}")
        self.governed_writes += 1
        self.decisions[decision_id] = {
            "decision_id": decision_id,
            "domain": domain,
            "category": category,
            "category_index": category_index,
            "recommended_action": recommended_action,
            "recommended_index": recommended_index,
            "action": recommended_action,
            "confidence": confidence,
            "probabilities": list(probabilities),
            "factor_vector": list(factor_vector),
            "factor_names": list(factor_names),
            "factors": {
                name: value
                for name, value in zip(factor_names, factor_vector)
            },
            "metadata": metadata,
            "status": "pending",
            "source": source,
            "scorer_version": scorer_version,
            "preset_version": preset_version,
            "factor_schema_version": factor_schema_version,
        }

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict[str, Any] | None = None,
        domain: str | None = None,
    ) -> None:
        if domain is not None and domain != self.domain:
            raise ValueError(f"unknown domain: {domain}")
        decision = self.decisions.get(decision_id)
        if decision is None:
            raise KeyError(decision_id)
        if decision.get("status") != "pending":
            raise ValueError(f"outcome already exists for decision_id: {decision_id}")
        self.outcome_writes += 1
        decision["actual_action"] = actual_action
        decision["is_correct"] = bool(is_correct)
        decision["outcome_metadata"] = dict(metadata or {})
        decision["outcome"] = "confirmed" if is_correct else "overridden"
        decision["status"] = decision["outcome"]

    def append_evidence_receipt(
        self,
        receipt_intent_id: str,
        domain: str,
        decision_id: str,
        canonical_payload: dict[str, Any],
        actor: str,
        source_route: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[int, str]:
        if self.fail_evidence_receipt:
            raise RuntimeError("fake evidence receipt failure")
        payload_json = json.dumps(
            {
                "receipt_intent_id": receipt_intent_id,
                "domain": domain,
                "decision_id": decision_id,
                "canonical_payload": canonical_payload,
                "actor": actor,
                "source_route": source_route,
                "metadata": metadata or {},
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        chain_index = len([row for row in self.evidence_receipts if row["domain"] == domain])
        self.evidence_receipts.append(
            {
                "receipt_intent_id": receipt_intent_id,
                "domain": domain,
                "decision_id": decision_id,
                "chain_index": chain_index,
                "payload_hash": payload_hash,
                "canonical_payload": dict(canonical_payload),
                "actor": actor,
                "source_route": source_route,
                "metadata": dict(metadata or {}),
            }
        )
        return chain_index, payload_hash

    def enqueue_to_outbox(
        self,
        domain: str,
        operation_type: str,
        target_key: str,
        payload: dict[str, Any],
        causal_decision_id: str | None = None,
    ) -> int:
        if self.fail_outbox:
            raise RuntimeError("fake outbox failure")
        outbox_id = len(self.outbox) + 1
        self.outbox.append(
            {
                "outbox_id": outbox_id,
                "domain": domain,
                "operation_type": operation_type,
                "target_key": target_key,
                "payload": dict(payload),
                "causal_decision_id": causal_decision_id,
            }
        )
        return outbox_id

    def get_decision(self, decision_id: str, domain: str | None = None) -> dict[str, Any] | None:
        if domain is not None and domain != self.domain:
            return None
        decision = self.decisions.get(decision_id)
        return dict(decision) if decision else None

    def get_archived_decisions(self, domain: str) -> list[dict[str, Any]]:
        assert domain == self.domain
        return [dict(decision) for decision in getattr(self, "_archive", [])]

    def get_all_decisions(self, domain: str) -> list[dict[str, Any]]:
        return [
            dict(decision)
            for decision in self.decisions.values()
            if decision.get("domain") == domain
        ]

    def get_verified_decisions(self, domain: str) -> list[dict[str, Any]]:
        return [
            dict(decision)
            for decision in self.get_all_decisions(domain)
            if decision.get("status") in {"confirmed", "overridden"}
        ]

    def count_decisions(self, domain: str) -> int:
        return len(self.get_all_decisions(domain))

    def count_verified(self, domain: str) -> int:
        return len(self.get_verified_decisions(domain))

    def count_verified_decisions(self, domain: str) -> int:
        return self.count_verified(domain)

    def count_correct(self, domain: str) -> int:
        return sum(
            1
            for decision in self.get_verified_decisions(domain)
            if bool(decision.get("is_correct"))
        )

    def save_centroids(
        self,
        domain: str,
        category: str,
        centroids: Any,
        metadata: dict[str, Any] | None = None,
        decision_id: str | None = None,
        decision_time_start: str | None = None,
        decision_time_end: str | None = None,
    ) -> None:
        return None

    def load_latest_centroids(self, domain: str) -> Any | None:
        return None

    def get_centroid_checkpoints(
        self,
        domain: str,
        *,
        limit: int = 100,
        checkpoint_time_start: str | None = None,
        checkpoint_time_end: str | None = None,
        decision_time_start: str | None = None,
        decision_time_end: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        assert domain == self.domain
        return []

    def archive_old_decisions(self, domain: str, keep_recent: int = 800) -> int:
        return 0

    def count_archived(self, domain: str) -> int:
        return 0

    def link_decision_to_entity(
        self,
        decision_id: str,
        entity_id: str,
        edge_type: str = "DECIDED_ON",
    ) -> None:
        self.links.append(
            {
                "decision_id": decision_id,
                "entity_id": entity_id,
                "edge_type": edge_type,
            }
        )

    def get_decision_links(self, decision_id: str | None = None) -> list[dict[str, Any]]:
        if decision_id is None:
            return list(self.links)
        return [link for link in self.links if link["decision_id"] == decision_id]

    def query_context(self, entity_id: str, hops: int = 2) -> list[dict[str, Any]]:
        return []

    def close(self) -> None:
        return None


def _active_config() -> S2PActiveGraphConfig:
    return S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_ACTIVE_AGE_GRAPH": "protocol_v2_test_cutover_phase_b",
            "S2P_ACTIVE_AGE_DOMAIN": "s2p",
            "S2P_ACTIVE_AGE_TEST_MODE": "1",
        }
    )


def _product_config() -> S2PActiveGraphConfig:
    return S2PActiveGraphConfig.from_env(
        {
            "S2P_ACTIVE_GRAPH_BACKEND": "age",
            "S2P_ACTIVE_AGE_DSN": "postgresql://postgres:secret@127.0.0.1/db",
            "S2P_ACTIVE_AGE_GRAPH": "governed_copilot_graph",
            "S2P_ACTIVE_AGE_DOMAIN": "s2p",
            "S2P_ACTIVE_AGE_TEST_MODE": "0",
        }
    )


def _reset_app_state(*, active_store: Any | None = None, active_config: S2PActiveGraphConfig | None = None) -> None:
    app.state.s2p_active_graph_config = active_config or S2PActiveGraphConfig.from_env({})
    app.state.scorer = build_s2p_scorer(graph_store=active_store)
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    app.state.s2p_shadow = initialize_s2p_shadow_state(env={})
    s2p_router._clear_score_conservation_status_cache()


@pytest.fixture(autouse=True)
def reset_app_after_test():
    _reset_app_state()
    yield
    _reset_app_state()


def _active_age_store() -> tuple[S2PActiveAGEGraphStore, FakeAGEStore]:
    fake = FakeAGEStore()
    active = create_s2p_active_graph_store(_active_config(), store_factory=lambda **_: fake)
    assert isinstance(active, S2PActiveAGEGraphStore)
    return active, fake


def _product_age_store() -> tuple[S2PActiveAGEGraphStore, FakeAGEStore]:
    fake = FakeAGEStore()
    active = create_s2p_active_graph_store(_product_config(), store_factory=lambda **_: fake)
    assert isinstance(active, S2PActiveAGEGraphStore)
    assert active.active_phase == "product_decision_outcome_cutover"
    return active, fake


def test_active_age_test_mode_constructs_store_with_factory():
    calls: list[dict[str, Any]] = []
    fake = FakeAGEStore()

    def factory(**kwargs: Any) -> FakeAGEStore:
        calls.append(dict(kwargs))
        return fake

    active = create_s2p_active_graph_store(_active_config(), store_factory=factory)

    assert isinstance(active, S2PActiveAGEGraphStore)
    assert calls == [
        {
            "backend": "age",
            "domain": "s2p",
            "dsn": "postgresql://postgres:secret@127.0.0.1/db",
            "graph_name": "protocol_v2_test_cutover_phase_b",
            "env": {},
            "test_mode": True,
        }
    ]


def test_score_route_uses_active_age_store_and_preserves_response_shape():
    active, fake = _active_age_store()
    _reset_app_state(active_store=active, active_config=_active_config())
    client = TestClient(app)

    response = client.post("/api/s2p/score", json=VALID_SCORE_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert {"event_id", "category", "action", "confidence", "decision_id"} <= set(body)
    assert fake.governed_writes == 1
    assert body["decision_id"] in fake.decisions
    assert fake.decisions[body["decision_id"]]["status"] == "pending"
    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "age"
    assert status["sqlite_authoritative"] is False
    assert status["age_active"] is True


def test_outcome_route_uses_active_age_after_score_and_preserves_invariant():
    active, fake = _active_age_store()
    _reset_app_state(active_store=active, active_config=_active_config())
    client = TestClient(app)
    score = client.post("/api/s2p/score", json=VALID_SCORE_REQUEST).json()

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
    assert len(fake.evidence_receipts) == 1
    assert fake.evidence_receipts[0]["decision_id"] == score["decision_id"]
    assert fake.evidence_receipts[0]["chain_index"] == 0
    assert fake.evidence_receipts[0]["payload_hash"]
    assert fake.outcome_writes == 1
    assert fake.decisions[score["decision_id"]]["status"] == "confirmed"
    duplicate = TestClient(app, raise_server_exceptions=False).post(
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
    assert duplicate.status_code != 200


def test_learn_route_uses_active_age_after_score_and_preserves_invariant():
    active, fake = _active_age_store()
    _reset_app_state(active_store=active, active_config=_active_config())
    client = TestClient(app)
    score = client.post(
        "/api/s2p/score",
        json={**VALID_SCORE_REQUEST, "event_id": "S2P-CUTOVER-PHASE-B-LEARN"},
    ).json()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 200
    assert len(fake.evidence_receipts) == 1
    assert fake.evidence_receipts[0]["decision_id"] == score["decision_id"]
    assert fake.evidence_receipts[0]["source_route"] == "/api/learn"
    assert fake.outcome_writes == 1
    assert fake.decisions[score["decision_id"]]["status"] == "confirmed"
    duplicate = TestClient(app, raise_server_exceptions=False).post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )
    assert duplicate.status_code != 200


def test_active_age_shadow_conflict_rejects_before_store_construction(monkeypatch):
    constructed = False

    def factory(**kwargs: Any) -> FakeAGEStore:
        nonlocal constructed
        constructed = True
        return FakeAGEStore()

    config = _active_config()
    monkeypatch.setenv("S2P_SHADOW_AGE", "1")
    with pytest.raises(Exception, match="S2P_SHADOW_AGE"):
        create_s2p_active_graph_store(config, store_factory=factory)

    assert constructed is False


def test_preview_remains_read_only_under_active_age():
    active, fake = _active_age_store()
    _reset_app_state(active_store=active, active_config=_active_config())
    before = fake.count_decisions("s2p")

    response = TestClient(app).get("/api/s2p/preview/queue")

    assert response.status_code == 200
    assert fake.count_decisions("s2p") == before
    assert fake.governed_writes == 0


def test_rollback_to_sqlite_after_active_age_test_mode():
    active, fake = _active_age_store()
    _reset_app_state(active_store=active, active_config=_active_config())
    client = TestClient(app)
    active_response = client.post("/api/s2p/score", json=VALID_SCORE_REQUEST)
    assert active_response.status_code == 200
    assert fake.governed_writes == 1

    _reset_app_state()
    before = app.state.graph_store.count_decisions("s2p")
    sqlite_response = client.post(
        "/api/s2p/score",
        json={**VALID_SCORE_REQUEST, "event_id": "S2P-CUTOVER-PHASE-B-ROLLBACK"},
    )

    assert sqlite_response.status_code == 200
    assert app.state.graph_store.count_decisions("s2p") == before + 1
    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "sqlite"
    assert status["sqlite_authoritative"] is True


def test_product_active_age_score_outcome_and_status_with_fake_store():
    active, fake = _product_age_store()
    _reset_app_state(active_store=active, active_config=_product_config())
    client = TestClient(app)

    score_response = client.post(
        "/api/s2p/score",
        json={**VALID_SCORE_REQUEST, "event_id": "S2P-PRODUCT-ACTIVE-SCORE"},
    )
    assert score_response.status_code == 200
    score = score_response.json()
    assert fake.governed_writes == 1
    decision = fake.decisions[score["decision_id"]]
    assert decision["metadata"]["active_age_phase"] == "product_decision_outcome_cutover"

    outcome_response = client.post(
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

    assert outcome_response.status_code == 200
    assert len(fake.evidence_receipts) == 1
    assert fake.evidence_receipts[0]["decision_id"] == score["decision_id"]
    assert fake.outcome_writes == 1
    assert fake.decisions[score["decision_id"]]["status"] == "confirmed"
    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "age"
    assert status["age_active"] is True
    assert status["sqlite_authoritative"] is False
    assert status["age_graph_kind"] == "product"
    assert status["active_graph_name"] == "governed_copilot_graph"
    assert status["active_test_mode"] is False
    assert status["cutover_ready"] is True
    assert status["decision_outcome_cutover_ready"] is True
    assert status["full_audit_memory_ready"] is False
    assert status["migration_complete"] is False
    assert status["evidence_receipt_ready"] is False
    assert status["migration_backfill_status"] == "not_in_scope"
    assert status["receipt_mapping_status"] == "excluded_first_cutover"
    assert status["evidence_receipt_mapping_status"] == "design_required"
    assert "postgres:secret@" not in str(status)


def test_active_age_outbox_fallback_allows_outcome_after_durable_enqueue():
    active, fake = _active_age_store()
    fake.fail_evidence_receipt = True
    _reset_app_state(active_store=active, active_config=_active_config())
    client = TestClient(app)
    score = client.post(
        "/api/s2p/score",
        json={**VALID_SCORE_REQUEST, "event_id": "S2P-CUTOVER-PHASE-B-OUTBOX"},
    ).json()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 200
    assert fake.evidence_receipts == []
    assert len(fake.outbox) == 1
    assert fake.outbox[0]["operation_type"] == "append_evidence_receipt"
    assert fake.outbox[0]["causal_decision_id"] == score["decision_id"]
    assert fake.outcome_writes == 1


def test_active_age_receipt_and_outbox_failure_blocks_outcome():
    active, fake = _active_age_store()
    fake.fail_evidence_receipt = True
    fake.fail_outbox = True
    _reset_app_state(active_store=active, active_config=_active_config())
    client = TestClient(app)
    score = client.post(
        "/api/s2p/score",
        json={**VALID_SCORE_REQUEST, "event_id": "S2P-CUTOVER-PHASE-B-HARD-FAIL"},
    ).json()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": score["decision_id"],
            "actual_action": score["action"],
            "outcome": "confirmed",
        },
    )

    assert response.status_code == 503
    assert fake.evidence_receipts == []
    assert fake.outbox == []
    assert fake.outcome_writes == 0
    assert fake.decisions[score["decision_id"]]["status"] == "pending"


def test_product_active_age_preview_remains_read_only():
    active, fake = _product_age_store()
    _reset_app_state(active_store=active, active_config=_product_config())
    before = fake.count_decisions("s2p")

    response = TestClient(app).get("/api/s2p/preview/queue")

    assert response.status_code == 200
    assert fake.count_decisions("s2p") == before
    assert fake.governed_writes == 0


def test_product_active_age_rollback_proves_no_hidden_reconciliation():
    active, fake = _product_age_store()
    _reset_app_state(active_store=active, active_config=_product_config())
    client = TestClient(app)
    product_response = client.post(
        "/api/s2p/score",
        json={**VALID_SCORE_REQUEST, "event_id": "S2P-PRODUCT-ACTIVE-ROLLBACK"},
    )
    assert product_response.status_code == 200
    product_decision_id = product_response.json()["decision_id"]
    assert fake.get_decision(product_decision_id) is not None

    _reset_app_state()
    before_sqlite = app.state.graph_store.count_decisions("s2p")
    sqlite_response = client.post(
        "/api/s2p/score",
        json={**VALID_SCORE_REQUEST, "event_id": "S2P-SQLITE-AFTER-PRODUCT-ROLLBACK"},
    )

    assert sqlite_response.status_code == 200
    assert app.state.graph_store.count_decisions("s2p") == before_sqlite + 1
    assert app.state.graph_store.get_decision(product_decision_id) is None
    assert fake.get_decision(product_decision_id) is not None
    status = client.get("/api/s2p/graph/status").json()
    assert status["active_backend"] == "sqlite"
    assert status["sqlite_authoritative"] is True
    assert status["age_active"] is False
