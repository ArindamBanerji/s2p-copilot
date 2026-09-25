from __future__ import annotations

import numpy as np
import pytest

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors
from app.main import app
from app.routers import s2p_pvg
from app.routers.s2p_preview import _get_preview_simulation_scorer


def test_g036_production_rejects_pvg_fixture_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S2P_PROFILE", "production")
    path = s2p_pvg._data_path("celonis_process_data.json")
    assert s2p_pvg._load_candidate_json(path, {"available": False}) == {"available": False}


def test_g036_production_preview_queries_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("S2P_PROFILE", "production")
    for endpoint in ("queue", "suppliers"):
        response = TestClient(app).get(f"/api/s2p/preview/{endpoint}")
        assert response.status_code == 200
        assert response.json()["source"] == "graph"
        assert response.json()["total"] == 0


def test_g036_missing_factors_cause_abstention(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S2P_PROFILE", "production")
    with pytest.raises(ValueError, match="abstaining.*required factors"):
        compute_all_factors({"invoice_id": "INV-1", "category": "price_variance"})


def test_g036_preview_cannot_mutate_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S2P_PROFILE", "test")
    scorer = _get_preview_simulation_scorer()
    canonical_store = app.state.graph_store
    canonical_scorer = app.state.scorer
    before_decisions = list(canonical_store.get_all_decisions("s2p"))
    before_v = int(getattr(canonical_scorer, "decision_count", 0))
    before_centroids = np.array(canonical_scorer._canonical_mu, copy=True)
    factors = {name: 0.5 for name in S2PDomainConfig.factors}
    result = scorer.score(factors, S2PDomainConfig.categories[0])
    scorer.learn(result.decision_id, "hold_for_review", "confirmed")
    assert list(canonical_store.get_all_decisions("s2p")) == before_decisions
    assert int(getattr(canonical_scorer, "decision_count", 0)) == before_v
    np.testing.assert_array_equal(canonical_scorer._canonical_mu, before_centroids)


def test_g036_demo_preserves_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S2P_PROFILE", "test")
    pvg = s2p_pvg._load_candidate_json(
        s2p_pvg._data_path("celonis_process_data.json"), {}
    )
    assert isinstance(pvg, dict) and pvg
