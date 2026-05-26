from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.config import S2PDomainConfig
from app.main import app, build_s2p_scorer


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_scorer_state():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def test_export_200():
    response = client.get("/api/s2p/explorer/export/centroids")

    assert response.status_code == 200


def test_export_25_cells():
    payload = client.get("/api/s2p/explorer/export/centroids").json()

    assert payload["total_cells"] == 25
    assert len(payload["centroids"]) == 25


def test_export_shape_5_5_7():
    payload = client.get("/api/s2p/explorer/export/centroids").json()

    assert payload["tensor_shape"] == [5, 5, 7]
    assert payload["categories"] == list(S2PDomainConfig.categories)
    assert payload["actions"] == list(S2PDomainConfig.actions)
    assert payload["factors"] == list(S2PDomainConfig.factors)


def test_centroid_7_values():
    payload = client.get("/api/s2p/explorer/export/centroids").json()

    assert all(len(row["centroid"]) == 7 for row in payload["centroids"])


def test_csv_header():
    payload = client.get("/api/s2p/explorer/export/csv").json()

    assert payload["header"] == ["category", "action"] + list(S2PDomainConfig.factors)
    assert len(payload["header"]) == 9


def test_csv_25_rows():
    payload = client.get("/api/s2p/explorer/export/csv").json()

    assert payload["total_rows"] == 25
    assert len(payload["rows"]) == 25


def test_csv_rows_have_9_columns():
    payload = client.get("/api/s2p/explorer/export/csv").json()

    assert all(len(row) == 9 for row in payload["rows"])


def test_export_routes_mounted():
    paths = {route.path for route in app.routes}

    assert "/api/s2p/explorer/export/centroids" in paths
    assert "/api/s2p/explorer/export/csv" in paths


def test_export_no_config_mutation_smoke():
    before = (
        list(S2PDomainConfig.categories),
        list(S2PDomainConfig.actions),
        list(S2PDomainConfig.factors),
    )

    response = client.get("/api/s2p/explorer/export/centroids")

    after = (
        list(S2PDomainConfig.categories),
        list(S2PDomainConfig.actions),
        list(S2PDomainConfig.factors),
    )
    assert response.status_code == 200
    assert after == before
