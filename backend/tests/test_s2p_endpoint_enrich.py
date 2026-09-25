from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _score(event_id: str = "ENRICH-TEST") -> dict:
    response = client.post(
        "/api/s2p/score",
        json={
            "event_id": event_id,
            "category": "price_variance",
            "amount": 22426.73,
            "supplier_id": "SUP-001",
            "supplier_name": "Copper Forward",
            "contract_id": "CON-7",
            "amount_variance_ratio": 0.04,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_score_has_action_name() -> None:
    assert isinstance(_score("ENRICH-ACTION").get("action_name"), str)


def test_score_has_reasoning() -> None:
    assert isinstance(_score("ENRICH-REASON").get("reasoning"), str)


def test_score_has_rule_override() -> None:
    assert isinstance(_score("ENRICH-OVERRIDE").get("rule_override"), bool)


def test_score_has_prior_verified() -> None:
    assert isinstance(_score("ENRICH-COUNT").get("prior_verified_count"), int)


def test_score_cold_start_flag_is_consistent() -> None:
    payload = _score("ENRICH-COLD")
    assert payload["cold_start"] is (payload["prior_verified_count"] < 30)


def test_score_warm_no_cold_start_after_verified_history() -> None:
    payload = _score("ENRICH-WARM")
    if payload["prior_verified_count"] >= 30:
        assert payload["cold_start"] is False


def test_score_has_context() -> None:
    context = _score("ENRICH-CONTEXT").get("context")
    assert isinstance(context, dict)
    assert context["category"] == "price_variance"
    assert "domain_factors" in context


def test_compliance_has_extinction() -> None:
    payload = client.get("/api/s2p/evidence/compliance").json()
    assert isinstance(payload.get("extinction_classes"), list)


def test_compliance_has_timeline() -> None:
    payload = client.get("/api/s2p/evidence/compliance").json()
    assert isinstance(payload.get("class_timeline"), dict)


def test_budget_warm_flag() -> None:
    payload = client.get("/api/self/investigation-budget").json()
    assert isinstance(payload.get("warm"), bool)


def test_budget_has_allocations() -> None:
    payload = client.get("/api/self/investigation-budget").json()
    assert isinstance(payload.get("recent_allocations"), list)
    assert isinstance(payload.get("allocations"), list)


def test_twin_freeze_returns_200() -> None:
    response = client.post("/api/s2p/learning/frozen-twin/freeze", json={})
    assert response.status_code == 200, response.text


def test_twin_has_comparison_points() -> None:
    payload = client.get("/api/s2p/learning/frozen-twin").json()
    assert isinstance(payload.get("comparison_points"), list)


def test_twin_has_gap() -> None:
    payload = client.get("/api/s2p/learning/frozen-twin").json()
    assert "gap" in payload
    assert payload["gap"] is None or isinstance(payload["gap"], (int, float))


def test_twin_frozen_at_present_or_explicitly_unavailable() -> None:
    payload = client.get("/api/s2p/learning/frozen-twin").json()
    assert "frozen_at" in payload
    assert payload["frozen_at"] is None or isinstance(payload["frozen_at"], (int, float, str))
