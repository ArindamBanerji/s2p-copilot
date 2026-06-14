import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app, build_s2p_scorer
from app.routers import s2p_evidence
from app.domains.s2p.config import S2PDomainConfig
from app.services.s2p_evidence_templates import S2P_TEMPLATES
from app.services.s2p_trust_explanations import format_trust_explanation

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


class FakeTrustScorer:
    graph_store = None

    def __init__(self, weights=None):
        self._weights = weights

    def get_dk_weights(self):
        return self._weights

    def get_centroid(self, category, action):
        return [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]

    def get_category_phase(self, category):
        return "VARIANCE_LEARNING"

    def get_verified_count(self):
        return 250


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
    assert data["template"] == S2P_TEMPLATES[category].template
    assert data["rendered"]
    assert "{" not in data["rendered"]
    assert "variables" in data
    assert data["evidence"]["text"] == data["rendered"]
    assert data["situation_context"]["domain"] == "s2p"
    assert "trust_explanation" in data
    assert "trust_explanation" in data["evidence"]


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


def test_evidence_template_missing_invoice_safe_response():
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance", "invoice_id": "MISSING-INVOICE"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == "MISSING-INVOICE"
    assert data["rendered"]
    assert data["situation_context"]["warnings"]


def test_template_omitted_invoice_id_returns_safe_response():
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == "unknown"
    assert data["invoice_found"] is False
    assert data["rendered"]
    assert data["evidence"]["text"] == data["rendered"]
    assert "invoice_id" in data["evidence"]["missing_fields"]
    assert data["situation_context"]["warnings"]


def test_template_nonexistent_invoice_id_returns_safe_response():
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance", "invoice_id": "INV-DOES-NOT-EXIST"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == "INV-DOES-NOT-EXIST"
    assert data["invoice_found"] is False
    assert data["rendered"]
    assert data["evidence"]["text"] == data["rendered"]
    assert data["evidence"]["context_used"]["invoice_id"] == "INV-DOES-NOT-EXIST"
    assert data["situation_context"]["warnings"]


def test_template_response_includes_trust_explanation_pre_transition():
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance"},
    )

    assert response.status_code == 200
    data = response.json()
    trust = data["trust_explanation"]
    assert trust["trust_available"] is False
    assert trust["learning_message"]
    assert trust["factors"]
    assert trust["provenance"]["source"] == "context"
    assert trust["provenance"]["provenance_tier"] == "context"
    assert trust["dk_weight_provenance"]["source"] == "unavailable"
    assert trust["dk_weight_provenance"]["provenance_tier"] == "unavailable"
    assert all(factor["provenance_label"] for factor in trust["factors"])
    assert all(factor["dk_weight_provenance"]["source"] == "unavailable" for factor in trust["factors"])
    assert data["evidence"]["trust_explanation"] == trust


def test_template_response_with_mock_dk_weights_sorts_factors():
    original = getattr(app.state, "scorer", None)
    app.state.scorer = FakeTrustScorer(
        [[0.2, 0.95, 0.1, 0.4, 0.5, 0.8, 0.3]]
    )
    try:
        response = client.get(
            "/api/s2p/evidence/template",
            params={
                "category": "price_variance",
                "invoice_id": invoice_for_category("price_variance")["invoice_id"],
            },
        )
    finally:
        app.state.scorer = original

    assert response.status_code == 200
    trust = response.json()["trust_explanation"]
    assert trust["trust_available"] is True
    contributions = [factor["contribution"] for factor in trust["factors"]]
    assert contributions == sorted(contributions, reverse=True)
    assert trust["provenance"]["source"] == "context"
    assert trust["provenance"]["provenance_tier"] == "context"
    assert trust["dk_weight_provenance"]["source"] == "scorer"
    assert trust["dk_weight_provenance"]["provenance_tier"] == "learned"


def test_template_no_scorer_uses_learning_message():
    original = getattr(app.state, "scorer", None)
    if hasattr(app.state, "scorer"):
        delattr(app.state, "scorer")
    try:
        response = client.get(
            "/api/s2p/evidence/template",
            params={"category": "price_variance"},
        )
    finally:
        app.state.scorer = original

    assert response.status_code == 200
    trust = response.json()["trust_explanation"]
    assert trust["trust_available"] is False
    assert "learning factor reliability" in trust["learning_message"]


def test_template_malformed_dk_weights_safe():
    original = getattr(app.state, "scorer", None)
    app.state.scorer = FakeTrustScorer([["bad"]])
    try:
        response = client.get(
            "/api/s2p/evidence/template",
            params={"category": "price_variance"},
        )
    finally:
        app.state.scorer = original

    assert response.status_code == 200
    assert response.json()["trust_explanation"]["trust_available"] is False


def test_trust_factor_value_and_dk_weight_provenance_are_split():
    original = getattr(app.state, "scorer", None)
    app.state.scorer = FakeTrustScorer(
        [[0.2, 0.95, 0.1, 0.4, 0.5, 0.8, 0.3]]
    )
    try:
        response = client.get(
            "/api/s2p/evidence/template",
            params={
                "category": "price_variance",
                "invoice_id": invoice_for_category("price_variance")["invoice_id"],
            },
        )
    finally:
        app.state.scorer = original

    assert response.status_code == 200
    factor = response.json()["trust_explanation"]["factors"][0]
    assert factor["factor_value_provenance"]["source"] == "context"
    assert factor["factor_value_provenance"]["provenance_tier"] == "context"
    assert factor["factor_value_provenance"]["measured"] is False
    assert factor["dk_weight_provenance"]["source"] == "scorer"
    assert factor["dk_weight_provenance"]["provenance_tier"] == "learned"
    assert factor["dk_weight_provenance"]["measured"] is True


def test_trust_factor_pre_transition_dk_weight_provenance_learning():
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "price_variance"},
    )

    assert response.status_code == 200
    trust = response.json()["trust_explanation"]
    assert trust["trust_available"] is False
    factor = trust["factors"][0]
    assert factor["dk_weight_provenance"]["source"] == "unavailable"
    assert factor["dk_weight_provenance"]["provenance_tier"] == "unavailable"
    assert factor["dk_weight_provenance"]["measured"] is False
    assert "trusted factor" not in trust["summary"]
    assert "noisy factor" not in trust["summary"]


def test_fixture_factor_value_not_labeled_as_learned_weight():
    original = getattr(app.state, "scorer", None)
    app.state.scorer = FakeTrustScorer(
        [[0.2, 0.95, 0.1, 0.4, 0.5, 0.8, 0.3]]
    )
    try:
        response = client.get(
            "/api/s2p/evidence/template",
            params={
                "category": "price_variance",
                "invoice_id": invoice_for_category("price_variance")["invoice_id"],
            },
        )
    finally:
        app.state.scorer = original

    assert response.status_code == 200
    for factor in response.json()["trust_explanation"]["factors"]:
        assert factor["source"] == "context"
        assert factor["provenance_tier"] == "context"
        assert factor["factor_value_provenance"]["source"] == "context"
        assert factor["dk_weight_provenance"]["source"] == "scorer"


def test_template_known_invoice_returns_context_nodes_with_sources():
    invoice = invoice_for_category("contract_gap")
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": invoice["category"], "invoice_id": invoice["invoice_id"]},
    )

    assert response.status_code == 200
    context = response.json()["situation_context"]
    assert context["nodes"]
    assert all(node["source"] for node in context["nodes"])
    assert any(node["source"] == "fixture" for node in context["nodes"])
    assert all(node["properties"]["provenance_label"] for node in context["nodes"])
    assert all("measured" in node["properties"] for node in context["nodes"])


def test_template_fixture_supplier_context_has_provenance_label():
    invoice = invoice_for_category("contract_gap")
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": invoice["category"], "invoice_id": invoice["invoice_id"]},
    )

    assert response.status_code == 200
    nodes = response.json()["situation_context"]["nodes"]
    supplier = next(node for node in nodes if node["type"] == "supplier")
    assert supplier["source"] == "fixture"
    assert supplier["properties"]["provenance_label"] == "supplier context · integration pending"
    assert supplier["properties"]["provenance_tier"] == "context"
    assert supplier["properties"]["integration_status"] == "pending"
    assert supplier["properties"]["measured"] is False


def test_template_situation_context_evidence_chain_has_provenance():
    invoice = invoice_for_category("contract_gap")
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": invoice["category"], "invoice_id": invoice["invoice_id"]},
    )

    assert response.status_code == 200
    chain = response.json()["situation_context"]["evidence_chain"]
    assert chain
    for entry in chain:
        assert entry["provenance_label"]
        assert entry["provenance_tier"] in {"learned", "context", "unavailable"}
        assert isinstance(entry["measured"], bool)


def test_template_similarity_criteria_visible_when_graph_store_has_history():
    original = app.state.graph_store
    graph_store = FakeGraphStore()
    graph_store.decisions.extend(
        [
            {
                "decision_id": "D-2",
                "entity_id": "S2P-INV-9998",
                "category": "contract_gap",
                "recommended_action": "hold_for_review",
                "created_at": "2026-01-03T00:00:00Z",
                "metadata": {"invoice_id": "S2P-INV-9998", "supplier_id": "SUP-001"},
            },
            {
                "decision_id": "D-3",
                "entity_id": "S2P-INV-9999",
                "category": "price_variance",
                "recommended_action": "hold_for_review",
                "created_at": "2026-01-04T00:00:00Z",
                "metadata": {"invoice_id": "S2P-INV-9999", "supplier_id": "SUP-001"},
            },
        ]
    )
    app.state.graph_store = graph_store
    try:
        response = client.get(
            "/api/s2p/evidence/template",
            params={"category": "contract_gap", "invoice_id": "S2P-INV-0001"},
        )
    finally:
        app.state.graph_store = original

    assert response.status_code == 200
    context = response.json()["situation_context"]
    similar = [node for node in context["nodes"] if node["type"] == "similar_decision"]
    assert similar
    assert {node["properties"]["decision_id"] for node in similar} == {"D-2"}
    criteria = similar[0]["properties"]["similarity_criteria"]
    assert criteria["supplier_id"] == "SUP-001"
    assert criteria["category"] == "contract_gap"
    assert criteria["order_by"] == "created_at DESC"
    assert similar[0]["properties"]["similarity_label"] == "same supplier + same category"
    assert similar[0]["properties"]["source"] == "graph_store"
    assert similar[0]["properties"]["provenance_tier"] == "context"
    assert similar[0]["properties"]["verified"] is False
    assert "verified decisions" not in similar[0]["properties"]["provenance_label"]


def test_template_known_invoice_preserves_trust_fields():
    invoice = invoice_for_category("price_variance")
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": invoice["category"], "invoice_id": invoice["invoice_id"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert "trust_explanation" in data
    assert "trust_weighted_factors" in data
    assert "trust_available" in data
    assert "trust_learning_message" in data
    assert "trust_explanation" in data["evidence"]


def test_evidence_chain_is_counterfactually_faithful_to_scorer_inputs():
    scorer = build_s2p_scorer()
    factor_names = list(S2PDomainConfig.factors)
    factors = {name: 0.5 for name in factor_names}
    factors["amount_variance_ratio"] = 0.98
    weights = {name: 0.0 for name in factor_names}
    weights["amount_variance_ratio"] = 1.0
    centroid = {name: 0.5 for name in factor_names}

    explanation = format_trust_explanation(
        category="price_variance",
        recommended_action="hold_for_review",
        confidence=0.7,
        factor_values=factors,
        factor_names=factor_names,
        dk_weights=weights,
        centroid=centroid,
    )
    displayed_top_factor = explanation.factors[0].name
    assert displayed_top_factor == "amount_variance_ratio"
    assert displayed_top_factor in factors

    base = scorer.score_read_only(factors, "price_variance")
    changed_factors = dict(factors)
    changed_factors[displayed_top_factor] = 0.02
    changed = scorer.score_read_only(changed_factors, "price_variance")

    assert (
        base.action != changed.action
        or abs(base.confidence - changed.confidence) > 1e-6
        or base.probabilities != changed.probabilities
    )


def test_evidence_template_unknown_category_safe():
    response = client.get(
        "/api/s2p/evidence/template",
        params={"category": "unknown_category", "invoice_id": "S2P-INV-0001"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "unknown_category"
    assert "requires review" in data["rendered"]


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
    assert "unknown" in response.json()["rendered"]


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
