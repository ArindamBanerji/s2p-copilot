from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer
from app.routers import s2p_explorer


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_scorer_state(tmp_path):
    scorer = build_s2p_scorer(str(tmp_path / "s2p-import-test.db"))
    app.state.scorer = scorer
    app.state.graph_store = scorer.graph_store
    app.state.s2p_reward_function = scorer._reward_fn
    yield


def _valid_centroids(value: float = 0.5) -> list:
    return [
        [
            [float(value) for _factor in range(S2PDomainConfig.n_factors)]
            for _action in range(S2PDomainConfig.n_actions)
        ]
        for _category in range(S2PDomainConfig.n_categories)
    ]


def _post(payload: dict) -> object:
    return client.post("/api/s2p/explorer/import/centroids", json=payload)


def test_centroid_import_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/explorer/import/centroids" in paths


def test_missing_centroids_returns_400():
    response = _post({})

    assert response.status_code == 400
    assert "Missing centroids" in response.json()["detail"]


def test_non_list_centroids_returns_400():
    response = _post({"centroids": "not-a-list"})

    assert response.status_code == 400


def test_wrong_category_count_returns_400():
    centroids = _valid_centroids()
    response = _post({"centroids": centroids[:-1]})

    assert response.status_code == 400


def test_wrong_action_count_returns_400():
    centroids = _valid_centroids()
    centroids[0] = centroids[0][:-1]
    response = _post({"centroids": centroids})

    assert response.status_code == 400


def test_wrong_factor_count_returns_400():
    centroids = _valid_centroids()
    centroids[0][0] = centroids[0][0][:-2]
    response = _post({"centroids": centroids})

    assert response.status_code == 400


def test_ragged_category_action_nesting_returns_400():
    centroids = _valid_centroids()
    centroids[0][0] = centroids[0][0][:-1]
    centroids[0][1] = centroids[0][1] + [0.2]
    response = _post({"centroids": centroids})

    assert response.status_code == 400


@pytest.mark.parametrize(
    "bad_value",
        [
            None,
            "bad",
            True,
            -0.01,
            1.01,
        ],
    )
def test_invalid_values_return_400(monkeypatch, bad_value):
    monkeypatch.setattr(s2p_explorer, "_current_conservation_status", lambda _request: "GREEN")
    centroids = _valid_centroids()
    centroids[0][0][0] = bad_value

    response = _post({"centroids": centroids})

    assert response.status_code == 400


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_nonfinite_values_fail_helper_validation(bad_value):
    centroids = _valid_centroids()
    centroids[0][0][0] = bad_value

    error = s2p_explorer._validate_centroid_values(centroids)

    assert error is not None
    assert "finite" in error


def test_boundaries_pass_validation_but_gate_can_reject(monkeypatch):
    monkeypatch.setattr(s2p_explorer, "_current_conservation_status", lambda _request: "RED")
    centroids = _valid_centroids()
    centroids[0][0][0] = 0.0
    centroids[-1][-1][-1] = 1.0

    response = _post({"centroids": centroids})

    assert response.status_code == 409
    assert "requires GREEN" in response.json()["detail"]


def test_non_green_conservation_returns_409(monkeypatch):
    monkeypatch.setattr(s2p_explorer, "_current_conservation_status", lambda _request: "AMBER")

    response = _post({"centroids": _valid_centroids()})

    assert response.status_code == 409
    assert "AMBER" in response.json()["detail"]


def test_green_conservation_import_updates_isolated_centroids(monkeypatch):
    monkeypatch.setattr(s2p_explorer, "_current_conservation_status", lambda _request: "GREEN")
    graph_store = app.state.graph_store
    domain = getattr(graph_store, "domain", "s2p")
    verified_before = graph_store.count_verified(domain)
    total_before = graph_store.count_verified_decisions(domain)
    centroids = _valid_centroids(0.25)
    centroids[0][0][0] = 0.0
    centroids[-1][-1][-1] = 1.0

    response = _post({"centroids": centroids})

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported"] is True
    assert payload["checkpoint_saved"] is True
    assert payload["checkpoint_id"].startswith("CKP-IMPORT-")
    assert payload["shape"] == [
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    ]
    assert payload["n_cells"] == S2PDomainConfig.n_categories * S2PDomainConfig.n_actions
    assert payload["n_values"] == (
        S2PDomainConfig.n_categories
        * S2PDomainConfig.n_actions
        * S2PDomainConfig.n_factors
    )
    assert payload["conservation_status"] == "GREEN"
    assert np.asarray(app.state.scorer.gae_scorer.centroids).tolist() == centroids
    assert graph_store.count_verified(domain) == verified_before
    assert graph_store.count_verified_decisions(domain) == total_before


def test_export_reads_imported_centroids(monkeypatch):
    monkeypatch.setattr(s2p_explorer, "_current_conservation_status", lambda _request: "GREEN")
    centroids = _valid_centroids(0.33)
    centroids[0][0] = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]

    response = _post({"centroids": centroids})
    assert response.status_code == 200
    exported = client.get("/api/s2p/explorer/centroid/price_variance/auto_approve")

    assert exported.status_code == 200
    assert exported.json()["centroid"] == [*centroids[0][0], 0.5]


def test_checkpoint_failure_rolls_back(monkeypatch):
    monkeypatch.setattr(s2p_explorer, "_current_conservation_status", lambda _request: "GREEN")

    def fail_checkpoint(*_args, **_kwargs):
        raise RuntimeError("checkpoint down")

    monkeypatch.setattr(s2p_explorer, "_checkpoint_imported_centroids", fail_checkpoint)
    before = deepcopy(app.state.scorer.gae_scorer.centroids.tolist())

    response = _post({"centroids": _valid_centroids(0.9)})

    assert response.status_code == 500
    assert "checkpoint failed" in response.json()["detail"]
    assert app.state.scorer.gae_scorer.centroids.tolist() == before
