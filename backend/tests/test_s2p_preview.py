"""
tests/test_s2p_preview.py - S2P v2 preview endpoint tests.
"""

import os
import pathlib
import sys
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.s2p_shadow import S2PShadowConfig, S2PShadowDiagnostics, S2PShadowState

client = TestClient(app)
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _queue(limit: int | None = None):
    path = "/api/s2p/preview/queue"
    if limit is not None:
        path = f"{path}?limit={limit}"
    return client.get(path)


def test_queue_returns_200():
    assert _queue().status_code == 200


def test_queue_default_limit_5():
    data = _queue().json()
    assert data["total"] == 50
    assert data["showing"] == 5
    assert len(data["invoices"]) == 5


def test_queue_default_preview_has_action_diversity():
    data = _queue().json()
    preview_rows = data.get("exceptions") or data.get("invoices") or []
    actions = {
        row.get("scored_action") or row.get("action") or row.get("recommended_action")
        for row in preview_rows
    }
    actions.discard(None)
    actions.discard("")

    assert len(actions) >= 2


def test_queue_custom_limit():
    data = _queue(12).json()
    assert data["showing"] == 12
    assert len(data["invoices"]) == 12


def test_queue_invoices_have_required_fields():
    invoice = _queue().json()["invoices"][0]
    for key in (
        "invoice_id",
        "supplier_id",
        "supplier_name",
        "category",
        "amount",
        "po_reference",
        "variance_pct",
        "recommended_action",
        "confidence",
        "probabilities",
        "factors",
        "factor_vector",
        "ground_truth_action",
    ):
        assert key in invoice


def test_queue_factor_vector_length_matches_config():
    invoices = _queue(50).json()["invoices"]
    assert all(len(invoice["factor_vector"]) == S2PDomainConfig.n_factors for invoice in invoices)
    assert all(len(invoice["factors"]) == S2PDomainConfig.n_factors for invoice in invoices)


def test_queue_scorer_metadata():
    scorer = _queue().json()["scorer"]
    assert scorer["engine"] == "Graph Attention Engine"
    assert scorer["tensor_shape"] == f"({S2PDomainConfig.n_categories}, {S2PDomainConfig.n_actions}, {S2PDomainConfig.n_factors})"
    assert scorer["factors"] == S2PDomainConfig.factors
    assert "version" in scorer


def test_conservation_returns_200():
    assert client.get("/api/s2p/preview/conservation").status_code == 200


def test_conservation_has_status():
    data = client.get("/api/s2p/preview/conservation").json()
    assert data["status"] in ("GREEN", "AMBER", "RED")


def test_conservation_has_auto_approve_pct():
    data = client.get("/api/s2p/preview/conservation").json()
    assert 0.0 <= data["auto_approve_pct"] <= 100.0
    assert data["verified_decisions"] == 1000
    assert data["fixture_decisions"] == 50


def test_conservation_has_engine_version():
    data = client.get("/api/s2p/preview/conservation").json()
    assert "engine_version" in data


def test_compounding_returns_200():
    assert client.get("/api/s2p/preview/compounding").status_code == 200


def test_compounding_trajectory_has_20_points():
    data = client.get("/api/s2p/preview/compounding").json()
    assert len(data["trajectory"]) == 20


def test_compounding_accuracy_increases():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    assert trajectory[-1]["accuracy"] > trajectory[0]["accuracy"] + 0.01


def test_compounding_initial_accuracy():
    data = client.get("/api/s2p/preview/compounding").json()
    assert data["initial_accuracy"] == data["trajectory"][0]["accuracy"]
    assert data["source"] == "s2p_preview_simulation"
    assert data["source"] != "synthetic_demo"


def test_compounding_uses_s2p_tensor_shape():
    data = client.get("/api/s2p/preview/compounding").json()
    assert data["tensor_shape"] == [
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    ]


def test_compounding_accuracy_values_in_range():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    assert all(0.0 <= point["accuracy"] <= 1.0 for point in trajectory)


def test_compounding_points_ordered_by_decision_number():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    decision_numbers = [point["decision_number"] for point in trajectory]
    assert decision_numbers == sorted(decision_numbers)
    assert all(point["decisions"] == point["decision_number"] for point in trajectory)


def test_compounding_last_segment_improves_over_first_segment():
    trajectory = client.get("/api/s2p/preview/compounding").json()["trajectory"]
    segment_size = max(1, len(trajectory) // 5)
    first_avg = sum(point["accuracy"] for point in trajectory[:segment_size]) / segment_size
    last_avg = sum(point["accuracy"] for point in trajectory[-segment_size:]) / segment_size
    assert last_avg > first_avg + 0.01


def test_suppliers_returns_200():
    assert client.get("/api/s2p/preview/suppliers").status_code == 200


def test_suppliers_default_returns_all():
    data = client.get("/api/s2p/preview/suppliers").json()
    assert data["total"] == 10
    assert data["showing"] == 10
    assert len(data["suppliers"]) == 10


def test_suppliers_explicit_limit_2():
    data = client.get("/api/s2p/preview/suppliers?limit=2").json()
    assert data["total"] == 10
    assert data["showing"] == 2
    assert len(data["suppliers"]) == 2


def test_suppliers_have_required_fields():
    supplier = client.get("/api/s2p/preview/suppliers").json()["suppliers"][0]
    for key in (
        "supplier_id",
        "supplier_name",
        "region",
        "otif",
        "exception_rate",
        "lead_time",
        "financial_health_trend",
    ):
        assert key in supplier


def test_suppliers_chen_lin_present():
    suppliers = client.get("/api/s2p/preview/suppliers?limit=10").json()["suppliers"]
    assert any(supplier["supplier_name"] == "Chen-Lin Mfg" for supplier in suppliers)


def test_config_returns_200():
    assert client.get("/api/s2p/preview/config").status_code == 200


def test_config_tensor_shape():
    data = client.get("/api/s2p/preview/config").json()
    assert data["tensor_shape"] == f"({S2PDomainConfig.n_categories}, {S2PDomainConfig.n_actions}, {S2PDomainConfig.n_factors})"


def test_config_factors_count_matches_config():
    data = client.get("/api/s2p/preview/config").json()
    assert len(data["factors"]) == S2PDomainConfig.n_factors
    assert data["factors"] == S2PDomainConfig.factors


def test_queue_actions_are_v2():
    invoices = _queue(50).json()["invoices"]
    v2_actions = set(S2PDomainConfig.actions)
    legacy_actions = {"approve", "escalate", "reject", "review"}
    assert all(invoice["recommended_action"] in v2_actions for invoice in invoices)
    assert all(invoice["recommended_action"] not in legacy_actions for invoice in invoices)


def test_queue_categories_are_v2():
    invoices = _queue(50).json()["invoices"]
    v2_categories = set(S2PDomainConfig.categories)
    legacy_categories = {
        "maverick_spend",
        "supplier_risk",
        "contract_breach",
        "budget_overrun",
        "approval_bypass",
        "data_quality",
    }
    assert all(invoice["category"] in v2_categories for invoice in invoices)
    assert all(invoice["category"] not in legacy_categories for invoice in invoices)


def test_score_endpoint_is_canonical_too():
    canonical_payload = {
        "event_id": "E001",
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-001",
        "match_status": 0.9,
        "amount_variance_ratio": 0.08,
        "duplicate_score": 0.04,
        "supplier_exception_history": 0.05,
        "payment_terms_impact": 0.48,
        "commodity_index_correlation": 0.76,
        "tax_regulatory_compliance": 0.90,
    }
    score = client.post("/api/s2p/score", json=canonical_payload)
    preview = client.get("/api/s2p/preview/queue")

    assert score.status_code == 200
    assert preview.status_code == 200
    assert len(score.json()["factor_vector"]) == S2PDomainConfig.n_factors
    assert len(preview.json()["invoices"][0]["factor_vector"]) == S2PDomainConfig.n_factors


def test_reset_clears_cache():
    import app.routers.s2p_preview as preview_module

    assert client.get("/api/s2p/preview/queue").status_code == 200
    assert preview_module._invoices is not None
    assert preview_module._scored_invoices is None

    preview_module.reset_preview_state()
    assert preview_module._invoices is None
    assert preview_module._scored_invoices is None

    response = client.get("/api/s2p/preview/queue")
    assert response.status_code == 200
    assert response.json()["total"] == 50


def test_preview_module_has_no_profile_scorer_reference():
    source = (BACKEND_ROOT / "app" / "routers" / "s2p_preview.py").read_text(encoding="utf-8")
    assert "ProfileScorer" not in source


def test_preview_queue_uses_app_state_scorer(monkeypatch):
    import app.routers.s2p_preview as preview_module

    class SentinelScorer:
        def score(self, factors, category, metadata=None):
            raise AssertionError("preview queue must use score_read_only")

        def score_read_only(self, factors, category):
            return SimpleNamespace(
                action="auto_approve",
                action_index=0,
                confidence=0.99,
                probabilities=[1.0, 0.0, 0.0, 0.0, 0.0],
                decision_id="SENTINEL",
            )

    preview_module.reset_preview_state()
    monkeypatch.setattr(app.state, "scorer", SentinelScorer(), raising=False)
    response = client.get("/api/s2p/preview/queue?limit=1")
    data = response.json()

    assert response.status_code == 200
    assert data["invoices"][0]["recommended_action"] == "auto_approve"
    assert data["invoices"][0]["confidence"] == 0.99


def test_preview_queue_recomputes_after_live_scorer_state_changes(monkeypatch):
    import app.routers.s2p_preview as preview_module

    class StatefulScorer:
        def __init__(self):
            self.action = "auto_approve"
            self.confidence = 0.99
            self.calls = 0

        def score(self, factors, category, metadata=None):
            raise AssertionError("preview queue must use score_read_only")

        def score_read_only(self, factors, category):
            self.calls += 1
            return SimpleNamespace(
                action=self.action,
                action_index=0 if self.action == "auto_approve" else 1,
                confidence=self.confidence,
                probabilities=[1.0, 0.0, 0.0, 0.0, 0.0],
                decision_id=f"SENTINEL-{self.calls}",
            )

    scorer = StatefulScorer()
    preview_module.reset_preview_state()
    monkeypatch.setattr(app.state, "scorer", scorer, raising=False)

    first = client.get("/api/s2p/preview/queue?limit=1")
    first_calls = scorer.calls
    scorer.action = "hold_for_review"
    scorer.confidence = 0.77
    second = client.get("/api/s2p/preview/queue?limit=1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["invoices"][0]["recommended_action"] == "auto_approve"
    assert second.json()["invoices"][0]["recommended_action"] == "hold_for_review"
    assert second.json()["invoices"][0]["confidence"] == 0.77
    assert scorer.calls > first_calls


def test_preview_queue_limit_does_not_score_full_fixture(monkeypatch):
    import app.routers.s2p_preview as preview_module

    class CountingScorer:
        def __init__(self):
            self.calls = 0

        def score(self, factors, category, metadata=None):
            raise AssertionError("preview queue must use score_read_only")

        def score_read_only(self, factors, category):
            self.calls += 1
            action_index = self.calls % len(S2PDomainConfig.actions)
            action = S2PDomainConfig.actions[action_index]
            return SimpleNamespace(
                action=action,
                action_index=action_index,
                confidence=0.9 - (self.calls * 0.001),
                probabilities=[0.2, 0.2, 0.2, 0.2, 0.2],
                decision_id=f"COUNTING-{self.calls}",
            )

    scorer = CountingScorer()
    preview_module.reset_preview_state()
    monkeypatch.setattr(app.state, "scorer", scorer, raising=False)

    response = client.get("/api/s2p/preview/queue?limit=1")

    assert response.status_code == 200
    assert response.json()["total"] == 50
    assert response.json()["showing"] == 1
    assert scorer.calls == 10


def test_preview_queue_does_not_write_sqlite_decisions():
    import app.routers.s2p_preview as preview_module

    original_scorer = app.state.scorer
    original_graph_store = app.state.graph_store
    try:
        scorer = build_s2p_scorer()
        app.state.scorer = scorer
        app.state.graph_store = scorer.graph_store
        preview_module.reset_preview_state()

        before = scorer.graph_store.count_decisions("s2p")
        response = client.get("/api/s2p/preview/queue?limit=5")

        assert response.status_code == 200
        assert scorer.graph_store.count_decisions("s2p") == before
    finally:
        app.state.scorer = original_scorer
        app.state.graph_store = original_graph_store
        preview_module.reset_preview_state()


def test_preview_queue_preserves_live_learned_scorer_state():
    import numpy as np
    import app.routers.s2p_preview as preview_module

    original_scorer = app.state.scorer
    original_graph_store = app.state.graph_store
    try:
        scorer = build_s2p_scorer()
        factors = {
            name: 0.55
            for name in S2PDomainConfig.factors
        }
        decision = scorer.score(factors, "price_variance")
        scorer.learn(decision.decision_id, decision.action, "confirmed")
        learned_centroids = np.array(scorer._scorer.centroids, copy=True)

        app.state.scorer = scorer
        app.state.graph_store = scorer.graph_store
        preview_module.reset_preview_state()
        before = scorer.graph_store.count_decisions("s2p")
        response = client.get("/api/s2p/preview/queue?limit=5")

        assert response.status_code == 200
        assert scorer.graph_store.count_decisions("s2p") == before
        assert np.allclose(scorer._scorer.centroids, learned_centroids)
    finally:
        app.state.scorer = original_scorer
        app.state.graph_store = original_graph_store
        preview_module.reset_preview_state()


def test_preview_queue_does_not_write_age_shadow_decisions():
    import app.routers.s2p_preview as preview_module

    class FakeShadowStore:
        def __init__(self):
            self.governed_decisions = []

        def generate_decision_id(self, domain: str) -> str:
            return "S2P-PREVIEW-TEST"

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

        def get_decision(self, decision_id: str, domain: str | None = None):
            return next(
                (row for row in self.governed_decisions if row["decision_id"] == decision_id),
                None,
            )

        def write_outcome(
            self,
            decision_id: str,
            actual_action: str,
            is_correct: bool,
            metadata: dict[str, Any] | None = None,
            domain: str | None = None,
        ) -> None:
            raise AssertionError("preview shadow must not write outcomes")

        def get_archived_decisions(self, domain: str):
            return []

    original_shadow = app.state.s2p_shadow
    fake = FakeShadowStore()
    app.state.s2p_shadow = S2PShadowState(
        config=S2PShadowConfig(
            enabled=True,
            strict=False,
            dsn="postgresql://postgres:secret@127.0.0.1/db",
            graph="protocol_v2_test_shadow",
            domain="s2p",
            test_mode=True,
        ),
        diagnostics=S2PShadowDiagnostics(max_events=10),
        store=fake,
    )
    try:
        preview_module.reset_preview_state()
        response = client.get("/api/s2p/preview/queue?limit=5")

        assert response.status_code == 200
        assert fake.governed_decisions == []
    finally:
        app.state.s2p_shadow = original_shadow
        preview_module.reset_preview_state()
