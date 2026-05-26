import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app, build_s2p_scorer
from app.domains.s2p.config import S2PDomainConfig
from app.routers import s2p_evidence

client = TestClient(app)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class FakeGraphStore:
    def __init__(self):
        self.domain = "s2p"
        self.decisions = [
            {
                "decision_id": "D-1",
                "entity_id": "S2P-INV-0001",
                "category": "contract_gap",
                "recommended_action": "escalate_to_buyer",
                "metadata": {"invoice_id": "S2P-INV-0001"},
            }
        ]

    def get_all_decisions(self, domain):
        assert domain == self.domain
        return list(self.decisions)

    def get_decision(self, decision_id):
        for decision in self.decisions:
            if decision["decision_id"] == decision_id:
                return decision
        return None


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_reward_function = app.state.scorer._reward_fn


def invoice_for_category(category):
    invoices = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))
    return next(invoice for invoice in invoices if invoice["category"] == category)


def assert_template_for_category(category):
    invoice = invoice_for_category(category)
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": category, "invoice_id": invoice["invoice_id"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == invoice["invoice_id"]
    assert data["category"] == category
    assert data["template"] == S2PDomainConfig.evidence_templates[category]
    assert data["rendered"]
    assert "{" not in data["rendered"]
    assert "variables" in data


def test_evidence_template_price_variance():
    assert_template_for_category("price_variance")


def test_evidence_template_quantity_mismatch():
    assert_template_for_category("quantity_mismatch")


def test_evidence_template_duplicate_risk():
    assert_template_for_category("duplicate_risk")


def test_evidence_template_contract_gap():
    assert_template_for_category("contract_gap")


def test_evidence_template_format_compliance():
    assert_template_for_category("format_compliance")


def test_evidence_template_missing_invoice_404():
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance", "invoice_id": "MISSING-INVOICE"},
    )
    assert response.status_code == 404


def test_evidence_template_missing_variable_renders_na(monkeypatch):
    monkeypatch.setattr(
        s2p_evidence,
        "_load_invoices",
        lambda: [
            {
                "invoice_id": "MINIMAL-INVOICE",
                "supplier_id": "SUP-MIN",
                "amount": 100.0,
                "category": "price_variance",
                "factors": {},
            }
        ],
    )
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance", "invoice_id": "MINIMAL-INVOICE"},
    )
    assert response.status_code == 200
    assert "N/A" in response.json()["rendered"]


def test_audit_trail_returns_decision_chain():
    original = app.state.graph_store
    app.state.graph_store = FakeGraphStore()
    try:
        response = client.get("/api/s2p/evidence/audit-trail/S2P-INV-0001")
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["decisions"][0]["decision_id"] == "D-1"


def test_audit_trail_returns_chain():
    original = app.state.graph_store
    app.state.graph_store = FakeGraphStore()
    try:
        response = client.get("/api/s2p/evidence/audit-trail/S2P-INV-0001")
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_audit_trail_unknown_invoice_empty():
    original = app.state.graph_store
    app.state.graph_store = FakeGraphStore()
    try:
        response = client.get("/api/s2p/evidence/audit-trail/UNKNOWN")
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    assert response.json()["decisions"] == []


def test_audit_trail_finds_score_created_decision_by_invoice_id():
    reset_sdk_scorer()
    invoice = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))[0]
    payload = {
        "event_id": invoice["invoice_id"],
        "category": invoice["category"],
        "amount": invoice["amount"],
        "supplier_id": invoice["supplier_id"],
    }

    score_response = client.post("/api/s2p/score", json=payload)
    assert score_response.status_code == 200
    decision_id = score_response.json()["decision_id"]

    audit_response = client.get(f"/api/s2p/evidence/audit-trail/{invoice['invoice_id']}")

    assert audit_response.status_code == 200
    data = audit_response.json()
    assert data["count"] >= 1
    matched = [
        decision
        for decision in data["decisions"]
        if decision.get("decision_id") == decision_id
    ]
    assert matched
    assert matched[0]["metadata"]["invoice_id"] == invoice["invoice_id"]
    assert matched[0]["invoice_id"] == invoice["invoice_id"]


def test_rules_returns_lifecycle():
    response = client.get("/api/s2p/evidence/rules")

    assert response.status_code == 200
    data = response.json()
    states = {rule["state"] for rule in data["rules"]}
    assert {"proposed", "shadow", "promoted", "rejected"}.issubset(states)
    assert data["source"] == "fixture"


def test_compliance_returns_summary():
    response = client.get("/api/s2p/evidence/compliance")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    assert 0.0 <= data["compliant_pct"] <= 1.0


def test_compliance_includes_flagged_count():
    response = client.get("/api/s2p/evidence/compliance")

    assert response.status_code == 200
    data = response.json()
    assert data["flagged_count"] == len(data["flagged_invoices"]) or data["flagged_count"] >= 20


def test_compliance_empty_invoice_list(monkeypatch):
    monkeypatch.setattr(s2p_evidence, "_load_invoices", lambda: [])

    response = client.get("/api/s2p/evidence/compliance")

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_all_evidence_endpoints_200():
    paths = [
        "/api/s2p/evidence/audit-trail/S2P-INV-0001",
        "/api/s2p/evidence/rules",
        "/api/s2p/evidence/compliance",
    ]

    for path in paths:
        assert client.get(path).status_code == 200
