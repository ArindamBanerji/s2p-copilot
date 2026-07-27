import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.main import app, build_s2p_scorer
from app.routers.s2p_explorer import _find_scored_decision


client = TestClient(app)

SCORE_BODY = {
    "event_id": "ROUTER-EXPLORER-001",
    "category": "duplicate_risk",
    "amount": 1250.0,
    "supplier_id": "SUP-001",
    "match_status": 0.8,
    "amount_variance_ratio": 0.3,
    "duplicate_score": 0.9,
    "supplier_exception_history": 0.2,
    "payment_terms_impact": 0.5,
    "commodity_index_correlation": 0.4,
    "tax_regulatory_compliance": 0.7,
}


def assert_json_safe(value):
    if isinstance(value, dict):
        for item in value.values():
            assert_json_safe(item)
    elif isinstance(value, list):
        for item in value:
            assert_json_safe(item)
    elif isinstance(value, float):
        assert math.isfinite(value)
    else:
        assert value is None or isinstance(value, (str, int, bool))


def assert_dict_response(response):
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)
    return data


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_graph_reader = S2PGraphReader(store=app.state.scorer.graph_store)
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def test_explorer_export_centroids_returns_expected_tensor_shape():
    reset_sdk_scorer()
    data = assert_dict_response(client.get("/api/s2p/explorer/export/centroids"))

    assert data["tensor_shape"] == [
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    ]
    assert data["total_cells"] == 25
    assert len(data["centroids"]) == 25


def test_explorer_export_csv_endpoint_returns_json_rows():
    reset_sdk_scorer()
    data = assert_dict_response(client.get("/api/s2p/explorer/export/csv"))

    assert data["total_rows"] == 25
    assert data["header"][:2] == ["category", "action"]
    assert len(data["header"]) == 2 + S2PDomainConfig.n_factors


def test_explorer_centroid_returns_valid_category_action_pair():
    reset_sdk_scorer()
    data = assert_dict_response(client.get("/api/s2p/explorer/centroid/price_variance/auto_approve"))

    assert data["category_name"] == "price_variance"
    assert data["action_name"] == "auto_approve"
    assert len(data["centroid"]) == S2PDomainConfig.n_factors


def test_explorer_centroid_unknown_category_returns_404():
    reset_sdk_scorer()
    response = client.get("/api/s2p/explorer/centroid/unknown_category/auto_approve")

    assert response.status_code == 404


def test_explorer_centroid_unknown_action_returns_404():
    reset_sdk_scorer()
    response = client.get("/api/s2p/explorer/centroid/price_variance/not_an_action")

    assert response.status_code == 404
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)
    assert "Unknown action" in data["detail"]


def test_explorer_drift_returns_all_actions_for_category():
    reset_sdk_scorer()
    data = assert_dict_response(client.get("/api/s2p/explorer/drift/price_variance"))

    assert data["category_name"] == "price_variance"
    assert len(data["centroids"]) == 5


def test_explorer_drift_unknown_category_returns_404():
    reset_sdk_scorer()
    response = client.get("/api/s2p/explorer/drift/not_a_s2p_category")

    assert response.status_code == 404
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)


def test_explorer_dk_weights_returns_availability_flag():
    reset_sdk_scorer()
    data = assert_dict_response(client.get("/api/s2p/explorer/dk-weights"))

    assert "available" in data
    assert isinstance(data["factors"], list)


def test_explorer_contribution_uses_scored_invoice_id():
    reset_sdk_scorer()
    score = assert_dict_response(client.post("/api/s2p/score", json=SCORE_BODY))

    data = assert_dict_response(
        client.get("/api/s2p/explorer/contribution", params={"invoice_id": SCORE_BODY["event_id"]})
    )

    assert data["decision_id"] == score["decision_id"]
    assert len(data["contributions"]) == S2PDomainConfig.n_factors


def test_explorer_contribution_unknown_invoice_returns_404():
    reset_sdk_scorer()
    response = client.get("/api/s2p/explorer/contribution", params={"invoice_id": "UNKNOWN-INVOICE"})

    assert response.status_code == 404


def test_explorer_contribution_requires_invoice_id_query_param():
    reset_sdk_scorer()
    response = client.get("/api/s2p/explorer/contribution")

    assert response.status_code == 422
    data = response.json()
    assert isinstance(data, dict)
    assert_json_safe(data)
    assert "detail" in data


def test_explorer_reader_receives_decision_id_without_domain():
    class Reader:
        def __init__(self):
            self.decision_ids = []

        def get_decision(self, decision_id):
            self.decision_ids.append(decision_id)
            return None

        def get_all_decisions(self):
            return []

    reader = Reader()
    assert _find_scored_decision(reader, "EMPTY-INVOICE") is None
    assert reader.decision_ids == ["EMPTY-INVOICE"]


def test_explorer_graph_failure_maps_to_503():
    reset_sdk_scorer()

    class FailingReader(S2PGraphReader):
        def get_decision(self, _decision_id):
            raise GraphUnavailableError("graph down")

    app.state.s2p_graph_reader = FailingReader(store=app.state.scorer.graph_store)
    response = client.get("/api/s2p/explorer/contribution", params={"invoice_id": "ANY"})

    assert response.status_code == 503


def test_explorer_empty_reader_result_is_not_graph_failure():
    reset_sdk_scorer()

    class EmptyReader(S2PGraphReader):
        def get_decision(self, _decision_id):
            return None

        def get_all_decisions(self):
            return []

    app.state.s2p_graph_reader = EmptyReader(store=app.state.scorer.graph_store)
    response = client.get("/api/s2p/explorer/contribution", params={"invoice_id": "EMPTY"})

    assert response.status_code == 404
    assert response.json()["detail"] == "No score result for invoice_id. Score the invoice first."
