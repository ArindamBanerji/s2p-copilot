from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer


client = TestClient(app)


class RaisingDkWeights:
    @property
    def dk_weights(self):
        raise RuntimeError("dk unavailable")


@pytest.fixture(autouse=True)
def reset_scorer_state():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def _ranking_payload():
    response = client.get("/api/s2p/explorer/ranking")
    assert response.status_code == 200
    return response.json()


def _assert_ranking_contract(payload):
    ranked = payload["ranked"]
    weights = [row["weight"] for row in ranked]
    factors = [row["factor"] for row in ranked]

    assert payload["factors"] == list(S2PDomainConfig.factors)
    assert payload["n_factors"] == S2PDomainConfig.n_factors
    assert len(ranked) == S2PDomainConfig.n_factors
    assert sorted(factors) == sorted(S2PDomainConfig.factors)
    assert len(set(factors)) == S2PDomainConfig.n_factors
    assert weights == sorted(weights)
    assert [row["rank"] for row in ranked] == list(range(1, S2PDomainConfig.n_factors + 1))
    assert payload["swap_candidate"] == ranked[0]["factor"]
    assert payload["swap_candidate_weight"] == ranked[0]["weight"]
    assert payload["swap_candidate"] in payload["rationale"]
    assert "lowest discriminatory weight" in payload["rationale"]
    assert payload["weight_source"] in {"dk_weights", "centroid_variance", "uniform"}


def test_ranking_endpoint_returns_contract():
    _assert_ranking_contract(_ranking_payload())


def test_ranking_route_exists():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/explorer/ranking" in paths


def test_cold_start_does_not_crash():
    payload = _ranking_payload()

    _assert_ranking_contract(payload)
    assert payload["weight_source"] in {"centroid_variance", "uniform"}


def test_uniform_fallback_equal_weights_when_no_weights_or_centroids():
    app.state.scorer = SimpleNamespace()

    payload = _ranking_payload()
    weights = [row["weight"] for row in payload["ranked"]]

    _assert_ranking_contract(payload)
    assert payload["weight_source"] == "uniform"
    assert weights == [round(1.0 / S2PDomainConfig.n_factors, 6)] * S2PDomainConfig.n_factors


def test_dk_weights_produce_known_order():
    factors = list(S2PDomainConfig.factors)
    weights = [0.7, 0.2, 0.6, 0.1, 0.5, 0.3, 0.4]
    app.state.scorer = SimpleNamespace(dk_weights=weights)

    payload = _ranking_payload()
    ranked_factors = [row["factor"] for row in payload["ranked"]]

    _assert_ranking_contract(payload)
    assert payload["weight_source"] == "dk_weights"
    assert ranked_factors == [
        factors[3],
        factors[1],
        factors[5],
        factors[6],
        factors[4],
        factors[2],
        factors[0],
    ]


def test_centroid_variance_fallback_produces_known_order():
    factors = list(S2PDomainConfig.factors)
    centroids = np.zeros((2, 2, S2PDomainConfig.n_factors), dtype=float)
    centroids[:, :, 0] = [0.0, 0.0]
    centroids[:, :, 1] = [[0.0, 0.2], [0.0, 0.2]]
    centroids[:, :, 2] = [[0.0, 0.4], [0.0, 0.4]]
    centroids[:, :, 3] = [[0.0, 0.6], [0.0, 0.6]]
    centroids[:, :, 4] = [[0.0, 0.8], [0.0, 0.8]]
    centroids[:, :, 5] = [[0.0, 1.0], [0.0, 1.0]]
    centroids[:, :, 6] = [[0.0, 0.1], [0.0, 0.1]]
    app.state.scorer = SimpleNamespace(centroids=centroids)

    payload = _ranking_payload()
    ranked_factors = [row["factor"] for row in payload["ranked"]]

    _assert_ranking_contract(payload)
    assert payload["weight_source"] == "centroid_variance"
    assert ranked_factors == [
        factors[0],
        factors[6],
        factors[1],
        factors[2],
        factors[3],
        factors[4],
        factors[5],
    ]


def test_wrong_length_dk_weights_fall_back_gracefully():
    app.state.scorer = SimpleNamespace(dk_weights=[0.1, 0.2], centroids=None)

    payload = _ranking_payload()

    _assert_ranking_contract(payload)
    assert payload["weight_source"] == "uniform"


@pytest.mark.parametrize(
    "malformed_weights",
    [
        [0.1, "bad", 0.3, 0.4, 0.5, 0.6, 0.7],
        {"match_status": 0.1},
        {0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4, 4: 0.5, 5: 0.6, 6: 0.7},
        "0.1,0.2,0.3",
        "1234567",
        b"1234567",
        7,
    ],
)
def test_malformed_dk_weights_fall_back_without_crashing(malformed_weights):
    app.state.scorer = SimpleNamespace(dk_weights=malformed_weights, centroids=None)

    payload = _ranking_payload()

    _assert_ranking_contract(payload)
    assert payload["weight_source"] in {"centroid_variance", "uniform"}
    assert payload["weight_source"] != "dk_weights"


def test_raising_dk_weights_source_falls_back_without_crashing():
    app.state.scorer = RaisingDkWeights()

    payload = _ranking_payload()

    _assert_ranking_contract(payload)
    assert payload["weight_source"] in {"centroid_variance", "uniform"}
    assert payload["weight_source"] != "dk_weights"
