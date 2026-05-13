import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.domains.s2p.config import S2PDomainConfig
from app.main import app

client = TestClient(app)


def test_intents_returns_five_s2p_intents():
    response = client.get("/api/s2p/control-tower/intents")

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 5
    assert len(data["intents"]) == 5


def test_intent_fields_present():
    intent = client.get("/api/s2p/control-tower/intents").json()["intents"][0]

    assert {"name", "description", "categories", "route", "evidence_panels"}.issubset(intent)
    assert intent["evidence_panels"]


def test_taxonomy_covers_expected_categories():
    intents = client.get("/api/s2p/control-tower/intents").json()["intents"]
    categories = {category for intent in intents for category in intent["categories"]}

    assert set(S2PDomainConfig.categories) == categories


def test_category_mapping_price_variance():
    data = client.get(
        "/api/s2p/control-tower/classify",
        params={"invoice_id": "S2P-INV-0001", "category": "price_variance"},
    ).json()

    assert data["category"] == "price_variance"
    assert data["intent"] == "invoice_price_variance"
    assert data["route"] == "insight"


def test_duplicate_risk_maps_to_invoice_duplicate_risk():
    data = client.get(
        "/api/s2p/control-tower/classify",
        params={"invoice_id": "S2P-INV-0001", "category": "duplicate_risk"},
    ).json()

    assert data["intent"] == "invoice_duplicate_risk"
    assert "similar_invoices" in data["evidence_panels"]


def test_contract_gap_routes_to_evidence():
    data = client.get(
        "/api/s2p/control-tower/classify",
        params={"invoice_id": "S2P-INV-0001", "category": "contract_gap"},
    ).json()

    assert data["intent"] == "contract_compliance_gap"
    assert data["route"] == "evidence"


def test_queue_respects_limit():
    data = client.get("/api/s2p/control-tower/queue", params={"limit": 3}).json()

    assert data["showing"] == 3
    assert len(data["queue"]) == 3
    assert data["total"] >= 3


def test_queue_sorted_by_priority_descending():
    queue = client.get("/api/s2p/control-tower/queue", params={"limit": 20}).json()["queue"]
    priorities = [item["priority"] for item in queue]

    assert priorities == sorted(priorities, reverse=True)


def test_priority_fields_numeric():
    item = client.get("/api/s2p/control-tower/queue", params={"limit": 1}).json()["queue"][0]

    assert isinstance(item["priority"], (int, float))
    assert isinstance(item["amount"], (int, float))


def test_classify_with_invoice_id_returns_invoice_and_intent():
    data = client.get(
        "/api/s2p/control-tower/classify",
        params={"invoice_id": "S2P-INV-0001"},
    ).json()

    assert data["invoice_id"] == "S2P-INV-0001"
    assert data["intent"] in {intent["intent_id"] for intent in client.get("/api/s2p/control-tower/intents").json()["intents"]}
    assert set(data["factors"]) == set(S2PDomainConfig.factors)


def test_unknown_category_returns_validation_error():
    response = client.get("/api/s2p/control-tower/classify", params={"category": "unknown"})

    assert response.status_code == 422
    assert "Unknown S2P category" in response.json()["detail"]


def test_unknown_invoice_returns_404():
    response = client.get("/api/s2p/control-tower/classify", params={"invoice_id": "missing"})

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_control_tower_does_not_call_compounding_scorer(monkeypatch):
    def fail_score(*args, **kwargs):
        raise AssertionError("Control Tower must not score")

    monkeypatch.setattr(app.state.scorer, "score", fail_score)

    assert client.get("/api/s2p/control-tower/classify", params={"invoice_id": "S2P-INV-0001"}).status_code == 200
    assert client.get("/api/s2p/control-tower/queue", params={"limit": 2}).status_code == 200


def test_no_soc_imports_or_vocabulary_in_control_tower_router():
    text = Path("app/routers/s2p_control_tower.py").read_text(encoding="utf-8").lower()

    for forbidden in (
        "from app.domains.soc",
        "import soc",
        "credential_access",
        "lateral_movement",
        "data_exfiltration",
        "escalate_soc",
        "suppress",
    ):
        assert forbidden not in text
