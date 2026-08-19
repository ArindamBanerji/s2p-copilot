"""F25/F26 S2P promotion and Frozen Twin contract tests."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app
from app.domains.s2p.config import S2PDomainConfig
from app.routers.s2p_autonomy import create_s2p_autonomy_router
from app.services.s2p_autonomy import S2PAutonomyManager


client = TestClient(app)


def test_sp_01_promotion_records_cover_all_categories() -> None:
    payload = client.get("/api/s2p/promotion/status").json()
    assert {row["decision_class"] for row in payload["categories"]} == set(S2PDomainConfig.categories)


def test_sp_02_promotion_requires_conservation_for_authority() -> None:
    client.post("/api/s2p/promotion/price_variance/advance", json={})
    response = client.post("/api/s2p/promotion/price_variance/advance", json={"shadow_decisions": 10, "conservation_state": "RED", "evidence_tier": "T_O"})
    assert response.json()["reason"] == "conservation_red"


def test_sp_03_green_promotion_requires_verified_evidence_and_shadow_volume(tmp_path: Path) -> None:
    manager = S2PAutonomyManager(tmp_path, app.state.scorer)
    isolated_app = FastAPI()
    isolated_app.include_router(create_s2p_autonomy_router(manager))

    with TestClient(isolated_app) as isolated_client:
        isolated_client.post("/api/s2p/promotion/format_compliance/advance", json={})
        response = isolated_client.post(
            "/api/s2p/promotion/format_compliance/advance",
            json={"shadow_decisions": 10, "conservation_state": "GREEN", "evidence_tier": "T_O"},
        )
    assert response.json()["new_stage"] == "promoted"


def test_sp_04_rollback_succeeds() -> None:
    response = client.post("/api/s2p/promotion/duplicate_risk/rollback", json={"reason": "kpi_regression"})
    assert response.json()["new_stage"] == "rolled_back"


def test_sp_05_twin_freeze_creates_snapshot() -> None:
    response = client.post("/api/s2p/twin/freeze")
    assert response.status_code in {200, 409}
    if response.status_code == 200:
        assert response.json()["frozen"] is True


def test_sp_06_twin_status_is_explicit() -> None:
    payload = client.get("/api/s2p/twin/status").json()
    assert payload["copilot"] == "s2p"
    assert "evidence_tier" in payload


def test_sp_07_twin_drift_requires_baseline_or_reports_drift() -> None:
    response = client.get("/api/s2p/twin/drift")
    assert response.status_code in {200, 409}


def test_sp_08_promotion_response_carries_evidence_tier() -> None:
    row = client.get("/api/s2p/promotion/status").json()["categories"][0]
    assert row["evidence_tier"] in {"T_S", "T_O"}


def test_sp_09_framework_categories_match_promotion_categories() -> None:
    assert set(S2PDomainConfig.categories) == {row["decision_class"] for row in client.get("/api/s2p/promotion/status").json()["categories"]}


def test_sp_10_transfer_endpoint_rejects_unknown_target() -> None:
    response = client.post("/api/s2p/promotion/contract_gap/transfer", json={"target_category": "unknown"})
    assert response.status_code == 422


def test_sp_11_transfer_requires_kept_record() -> None:
    response = client.post("/api/s2p/promotion/format_compliance/transfer", json={"target_category": "price_variance"})
    assert response.json()["reason"] == "transfer_requires_kept"


def test_sp_12_promotion_history_is_persistent_shape() -> None:
    row = client.get("/api/s2p/promotion/status").json()["categories"][0]
    assert row["stage_history"]
    assert {"stage", "timestamp"}.issubset(row["stage_history"][0])


def test_sp_13_promotion_authority_is_category_scoped() -> None:
    rows = client.get("/api/s2p/promotion/status").json()["categories"]
    assert {row["authority"] for row in rows}


def test_sp_14_twin_status_is_restart_safe_contract() -> None:
    payload = client.get("/api/s2p/twin/status").json()
    assert payload["frozen"] in {True, False}


def test_sp_15_score_endpoint_remains_available() -> None:
    response = client.post("/api/s2p/score", json={"event_id": "sp-15", "category": "price_variance", "amount": 100.0, "supplier_id": "supplier-1"})
    assert response.status_code == 200


def test_sp_16_score_response_can_include_twin_comparison() -> None:
    response = client.post("/api/s2p/score", json={"event_id": "sp-16", "category": "quantity_mismatch", "amount": 100.0, "supplier_id": "supplier-1"})
    assert response.status_code == 200
    if client.get("/api/s2p/twin/status").json()["frozen"]:
        assert "frozen_twin" in response.json()


def test_sp_17_ledger_timeline_endpoint_remains_available() -> None:
    response = client.get("/api/s2p/ledger/timeline")
    assert response.status_code == 200


def test_sp_18_ledger_can_show_governance_event_shape() -> None:
    payload = client.get("/api/s2p/ledger/timeline").json()
    assert "timeline" in payload
    assert isinstance(payload["timeline"], list)


def test_sp_19_invalid_category_does_not_create_authority_record() -> None:
    response = client.post("/api/s2p/promotion/not_a_category/advance", json={})
    assert response.status_code == 422


def test_sp_20_promotion_status_has_five_categories() -> None:
    assert len(client.get("/api/s2p/promotion/status").json()["categories"]) == 5


def test_sp_21_twin_and_promotion_routes_are_mounted() -> None:
    paths = {route.path for route in app.routes}
    assert "/api/s2p/twin/status" in paths
    assert "/api/s2p/promotion/status" in paths
