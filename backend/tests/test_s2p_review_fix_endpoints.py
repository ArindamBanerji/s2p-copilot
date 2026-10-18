from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_fingerprint_returns_json_for_fixture_invoice() -> None:
    response = client.get(
        "/api/s2p/insight/fingerprint",
        params={"invoice_id": "S2P-INV-0001"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["invoice_id"] == "S2P-INV-0001"
    assert payload["factors"]
    assert isinstance(payload["fingerprint_available"], bool)
    assert isinstance(payload["degraded_factors"], list)


def test_fingerprint_unknown_invoice_returns_default_json() -> None:
    response = client.get(
        "/api/s2p/insight/fingerprint",
        params={"invoice_id": "UNKNOWN"},
    )

    assert response.status_code == 200
    assert response.json()["invoice_id"] == "UNKNOWN"
    assert response.json()["error"] == "invoice not found"


def test_decision_explorer_returns_decision_summary() -> None:
    response = client.get("/api/s2p/insight/decision-explorer")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert {
        "categories",
        "decisions",
        "accuracy",
        "total_decisions",
        "total_verified",
    }.issubset(response.json())


def test_rule_lifecycle_returns_rules_stages_and_promotions() -> None:
    response = client.get("/api/s2p/evidence/rule-lifecycle")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["rules"]
    assert payload["stages"]
    assert payload["promotions"]
    assert payload["evolution_ledger"]
