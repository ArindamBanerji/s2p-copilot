from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.domains.s2p import factors as s2p_factors
from app.main import app
from app.routers import s2p_demo_beats, s2p_evidence, s2p_insight, s2p_suppliers
from app.routers.s2p_demo_beats import _rows


@pytest.fixture(autouse=True)
def clear_signal_cache():
    from app.services import cross_copilot_signals
    cross_copilot_signals._OFFLINE_CACHE.clear()
    yield
    cross_copilot_signals._OFFLINE_CACHE.clear()


@pytest.mark.parametrize("bad_value", [RuntimeError("offline"), None])
def test_demo_rows_marks_unavailable_for_exception_or_malformed(monkeypatch, bad_value):
    reader = MagicMock()
    if isinstance(bad_value, BaseException):
        reader.get_all_decisions.side_effect = bad_value
    else:
        reader.get_all_decisions.return_value = bad_value
    rows, available = _rows(reader, return_status=True)
    assert rows == []
    assert available is False


def test_demo_rows_distinguishes_legitimate_empty():
    reader = MagicMock()
    reader.get_all_decisions.return_value = []
    assert _rows(reader, return_status=True) == ([], True)


@pytest.mark.parametrize("bad_rows", [RuntimeError("offline"), None])
def test_frozen_twin_marks_graph_unavailable(monkeypatch, bad_rows):
    reader = MagicMock()
    if isinstance(bad_rows, BaseException):
        reader.get_all_decisions.side_effect = bad_rows
        reader.get_verified_decisions.side_effect = bad_rows
    else:
        reader.get_all_decisions.return_value = bad_rows
        reader.get_verified_decisions.return_value = bad_rows
    monkeypatch.setattr(s2p_demo_beats, "_reader", lambda _request: reader)
    monkeypatch.setattr(s2p_demo_beats, "_scorer", lambda _request: MagicMock())
    monkeypatch.setattr(
        s2p_demo_beats, "_centroids",
        lambda _scorer: s2p_demo_beats.np.asarray(
            s2p_demo_beats.S2PDomainConfig.get_profile_centroids(), dtype=float,
        ),
    )
    request = MagicMock()
    request.app.state.s2p_autonomy = None
    payload = s2p_demo_beats.frozen_twin(request)
    assert payload["data_available"] is False
    assert payload["reason"] == "graph_unavailable"
    assert payload["current_accuracy"] == 0.0


@pytest.mark.parametrize("bad_value", [RuntimeError("offline"), None])
def test_fingerprint_marks_factor_failure(monkeypatch, bad_value):
    monkeypatch.setattr(s2p_insight, "compute_all_factors", lambda *_args, **_kwargs: bad_value)
    payload = s2p_insight.fingerprint_payload({"invoice_id": "I-1", "category": "price_variance"}, "I-1")
    assert payload["fingerprint_available"] is False
    assert payload["factors"] == {}
    assert payload["degraded_factors"]


@pytest.mark.parametrize("bad_value", [RuntimeError("factor failure"), None])
def test_compliance_marks_factor_failure(monkeypatch, bad_value):
    monkeypatch.setattr(s2p_evidence, "compute_all_factors", lambda *_args, **_kwargs: bad_value)
    payload = s2p_evidence.compliance_payload([{"invoice_id": "I-1"}], None)
    assert payload["compliance_available"] is False
    assert payload["degraded_factors"]
    assert payload["flagged_invoices"][0]["tax_regulatory_compliance"] == 1.0


def test_compliance_endpoint_degrades_when_production_invoice_misses_factor(monkeypatch):
    invoice = {
        "invoice_id": "I-MISSING-FACTOR",
        "category": "price_variance",
        "supplier_id": "SUP-1",
        **{name: 0.1 for name in s2p_factors.FACTOR_NAMES if name != "environmental_risk"},
    }
    monkeypatch.setattr(s2p_factors, "_production_profile", lambda: True)
    monkeypatch.setattr(s2p_evidence, "_load_invoices", lambda: [invoice])
    monkeypatch.setattr(app.state, "materializer", None, raising=False)

    response = TestClient(app).get("/api/s2p/evidence/compliance")

    assert response.status_code == 200
    assert response.json()["compliance_available"] is False


@pytest.mark.parametrize("bad_value", [None, {"unexpected": []}])
def test_cross_graph_does_not_fabricate_without_celonis(monkeypatch, bad_value):
    monkeypatch.setattr(s2p_insight, "_load_celonis", lambda: bad_value)
    monkeypatch.setattr(s2p_insight, "_load_suppliers", lambda: [])
    payload = s2p_insight.cross_graph()
    assert payload["celonis_available"] is False
    assert payload["insights"] == []
    assert payload["narrative"] == "Process data unavailable."


def test_trend_none_is_stable_but_unavailable():
    assert s2p_suppliers._trend_direction(None, return_status=True) == ("stable", False)


def test_trend_stable_value_is_available():
    assert s2p_suppliers._trend_direction(0.0, return_status=True) == ("stable", True)
