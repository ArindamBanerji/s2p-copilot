"""S2P preview observation wiring tests."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.routers import s2p as s2p_router
from app.routers import s2p_preview


def _sqlite_count(store, table: str) -> int:
    return int(
        store.connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE domain = ?",
            ("s2p",),
        ).fetchone()[0]
    )


def _observation_rows(store) -> list[dict]:
    return [
        dict(row)
        for row in store.connection.execute(
            "SELECT * FROM observations WHERE domain = ? ORDER BY created_at",
            ("s2p",),
        ).fetchall()
    ]


def _factor_vector_rows(store) -> list[dict]:
    return [
        dict(row)
        for row in store.connection.execute(
            """
            SELECT * FROM observation_factor_vectors
            WHERE domain = ?
            ORDER BY created_at
            """,
            ("s2p",),
        ).fetchall()
    ]


def _edge_rows(store) -> list[dict]:
    return [
        dict(row)
        for row in store.connection.execute(
            """
            SELECT * FROM observation_entity_edges
            WHERE domain = ?
            ORDER BY created_at
            """,
            ("s2p",),
        ).fetchall()
    ]


def _score_payload(event_id: str) -> dict:
    return {
        "event_id": event_id,
        "category": "price_variance",
        "amount": 5000.0,
        "supplier_id": "SUP-OBS",
        "match_status": 0.95,
        "amount_variance_ratio": 0.05,
        "duplicate_score": 0.02,
        "supplier_exception_history": 0.03,
        "payment_terms_impact": 0.50,
        "commodity_index_correlation": 0.80,
        "tax_regulatory_compliance": 0.95,
    }


def test_preview_queue_writes_observations_not_decisions_or_conservation():
    original_scorer = app.state.scorer
    original_graph_store = app.state.graph_store
    try:
        scorer = build_s2p_scorer(":memory:")
        app.state.scorer = scorer
        app.state.graph_store = scorer.graph_store
        s2p_preview.reset_preview_state()
        s2p_router._clear_score_conservation_status_cache()
        client = TestClient(app)

        before_decisions = scorer.graph_store.count_decisions("s2p")
        before_verified = scorer.graph_store.count_verified_decisions("s2p")
        before_observations = _sqlite_count(scorer.graph_store, "observations")
        before_conservation = client.get("/api/conservation/status").json()

        response = client.get("/api/s2p/preview/queue?limit=5")

        after_conservation = client.get("/api/conservation/status").json()
        assert response.status_code == 200
        data = response.json()
        assert "exceptions" in data
        assert "invoices" in data
        assert data["showing"] == 5
        assert scorer.graph_store.count_decisions("s2p") == before_decisions
        assert scorer.graph_store.count_verified_decisions("s2p") == before_verified
        assert after_conservation["total_decisions"] == before_conservation["total_decisions"]
        assert after_conservation["verified_count"] == before_conservation["verified_count"]
        assert _sqlite_count(scorer.graph_store, "observations") > before_observations

        observations = _observation_rows(scorer.graph_store)
        vectors = _factor_vector_rows(scorer.graph_store)
        edges = _edge_rows(scorer.graph_store)
        assert observations
        assert vectors
        assert edges
        assert all(row["observation_id"].startswith("OBS-") for row in observations)
        assert all(row["domain"] == "s2p" for row in observations)
        assert all(row["source_route"] == "preview" for row in observations)
        assert all(row["entity_id"] for row in edges)
        assert all(row["dimension"] == S2PDomainConfig.n_factors for row in vectors)
        assert all(row["factor_names"] for row in vectors)
        assert all(row["factor_vector_json"] for row in vectors)
    finally:
        app.state.scorer = original_scorer
        app.state.graph_store = original_graph_store
        s2p_preview.reset_preview_state()
        s2p_router._clear_score_conservation_status_cache()


def test_main_score_still_creates_decision():
    original_scorer = app.state.scorer
    original_graph_store = app.state.graph_store
    try:
        scorer = build_s2p_scorer(":memory:")
        app.state.scorer = scorer
        app.state.graph_store = scorer.graph_store
        s2p_router._clear_score_conservation_status_cache()
        client = TestClient(app)

        before_decisions = scorer.graph_store.count_decisions("s2p")
        response = client.post("/api/s2p/score", json=_score_payload("OBS-WIRING-SCORE-001"))

        assert response.status_code == 200
        assert response.json()["decision_id"]
        assert scorer.graph_store.count_decisions("s2p") == before_decisions + 1
    finally:
        app.state.scorer = original_scorer
        app.state.graph_store = original_graph_store
        s2p_router._clear_score_conservation_status_cache()
