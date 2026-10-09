from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.domains.s2p import factors
from app.domains.s2p.evolution.service import S2PEvolutionService
from app.services.cohort_status import S2PCohortStatus
from app.services.cross_copilot_signals import CrossCopilotSignalConsumer
from app.services.optimizer_export import OptimizerExportService
from app.services.s2p_preview_data import _verified_rows
from app.services.supplier_profile_accumulator import SupplierProfileAccumulator
from app.routers.s2p_data_helpers import load_json


@pytest.fixture(autouse=True)
def clear_signal_cache():
    from app.services import cross_copilot_signals
    cross_copilot_signals._OFFLINE_CACHE.clear()
    yield
    cross_copilot_signals._OFFLINE_CACHE.clear()


@pytest.mark.parametrize("value", [RuntimeError("offline"), None])
def test_verified_rows_marks_unavailable(monkeypatch, value):
    reader = MagicMock()
    if isinstance(value, BaseException):
        reader.get_verified_decisions.side_effect = value
    else:
        reader.get_verified_decisions.return_value = value
    monkeypatch.setattr("app.services.s2p_preview_data.S2PGraphReader", lambda _store: reader)
    assert _verified_rows(MagicMock(), return_status=True) == ([], False)


@pytest.mark.parametrize("value", [OSError("offline"), None])
def test_cross_copilot_signals_marks_unavailable(monkeypatch, value):
    response = MagicMock(status_code=200)
    if isinstance(value, BaseException):
        monkeypatch.setattr("app.services.cross_copilot_signals.httpx.get", MagicMock(side_effect=value))
    else:
        response.json.return_value = value
        monkeypatch.setattr("app.services.cross_copilot_signals.httpx.get", MagicMock(return_value=response))
    rows, available = CrossCopilotSignalConsumer("http://test").fetch_supplier_signals(
        "Sysco", return_status=True,
    )
    assert rows == []
    assert available is False


@pytest.mark.parametrize("scorer", [MagicMock(), None])
def test_optimizer_centroid_failure_is_marked(monkeypatch, scorer):
    if scorer is not None:
        scorer.get_centroid.side_effect = RuntimeError("offline")
    service = OptimizerExportService()
    monkeypatch.setattr(
        "app.services.optimizer_export._centroids_from_scorer",
        lambda _scorer: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    centroids, available = service._centroids(scorer, return_status=True)
    assert centroids
    assert available is False


@pytest.mark.parametrize("weights", [None, ["bad"]])
def test_optimizer_dk_failure_is_unavailable(weights):
    scorer = MagicMock()
    scorer.get_dk_weights.return_value = weights
    weights_result, available = OptimizerExportService()._dk_weights(scorer, return_status=True)
    assert weights_result is None
    assert available is False


def test_optimizer_metadata_uses_unavailable_status():
    metadata = OptimizerExportService()._compute_metadata({"dk_weights": [], "dk_available": False})
    assert metadata["dk_status"] == "unavailable"
    assert metadata["dk_available"] is False


def test_factor_exception_marks_factor_unavailable(monkeypatch):
    factor = MagicMock(name="factor")
    factor.name = "tax_regulatory_compliance"
    factor.compute.side_effect = RuntimeError("factor offline")
    monkeypatch.setattr(factors, "ALL_FACTORS", [factor])
    values, available, degraded = factors.compute_all_factors(
        {"tax_regulatory_compliance": 0.5}, return_status=True,
    )
    assert values["tax_regulatory_compliance"] == 0.5
    assert available is False
    assert degraded == ["tax_regulatory_compliance"]


@pytest.mark.parametrize("name", ["missing.json", "bad.json"])
def test_load_json_marks_fixture_failure(tmp_path, monkeypatch, name):
    if name == "bad.json":
        (tmp_path / name).write_text("{bad", encoding="utf-8")
    monkeypatch.setattr("app.routers.s2p_data_helpers._DATA_DIR", tmp_path)
    assert load_json(name, [], return_status=True) == ([], False)


def test_load_json_legitimate_empty_is_available(tmp_path, monkeypatch):
    (tmp_path / "empty.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr("app.routers.s2p_data_helpers._DATA_DIR", tmp_path)
    assert load_json("empty.json", [], return_status=True) == ([], True)


@pytest.mark.parametrize("content", [None, "[]"])
def test_evolution_fixture_failure_status(tmp_path, content):
    path = tmp_path / "fixture.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    service = S2PEvolutionService(MagicMock(), fixture_path=path)
    assert service.evolution_available is False


@pytest.mark.parametrize("path", ["missing.json", "bad.json"])
def test_supplier_profiles_fixture_failure(path, tmp_path):
    fixture = tmp_path / path
    if path == "bad.json":
        fixture.write_text("{}", encoding="utf-8")
    accumulator = SupplierProfileAccumulator(str(fixture))
    assert accumulator.profiles_available is False
    assert accumulator.get_all_profiles() == []


def test_supplier_profiles_fixture_empty_is_available(tmp_path):
    fixture = tmp_path / "empty.json"
    fixture.write_text("[]", encoding="utf-8")
    accumulator = SupplierProfileAccumulator(str(fixture))
    assert accumulator.profiles_available is True


@pytest.mark.parametrize("path_exists", [False, True])
def test_cohort_instrument_availability(tmp_path, path_exists):
    path = tmp_path / "oracle.json"
    if path_exists:
        path.write_text("[]", encoding="utf-8")
    status = S2PCohortStatus(oracle_artifact_path=path)
    result, available = status._load_instrument(return_status=True)
    assert available is path_exists
    assert result["reason"] == ("validation_failed" if path_exists else "instrument_unavailable")


def test_cohort_reader_unavailable_is_distinguished():
    result, available = S2PCohortStatus(reader=None)._read_decisions(return_status=True)
    assert result == []
    assert available is False


def test_cohort_reader_empty_is_available():
    reader = MagicMock()
    reader.get_verified_decisions.return_value = []
    result, available = S2PCohortStatus(reader=reader)._read_decisions(return_status=True)
    assert result == []
    assert available is True
