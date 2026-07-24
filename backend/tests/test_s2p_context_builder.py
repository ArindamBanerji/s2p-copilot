from __future__ import annotations

from copy import deepcopy

from app.domains.s2p.config import S2PDomainConfig
from app.routers.s2p_data_helpers import find_invoice
from app.services.s2p_context_builder import S2PContextBuilder
from copilot_sdk.graph.enrichment import ProvenancedValue


class FakeScorer:
    def __init__(self) -> None:
        self.instantiated = False

    def get_centroid(self, category: str, action: str):
        assert category in S2PDomainConfig.categories
        assert action in S2PDomainConfig.actions
        return [0.5] * len(S2PDomainConfig.factors)


class FakeGraphStore:
    domain = "s2p"

    def __init__(self) -> None:
        self.write_calls = 0
        self.decisions = [
            {
                "decision_id": "D-TARGET",
                "category": "contract_gap",
                "recommended_action": "escalate_to_buyer",
                "created_at": "2026-01-02T00:00:00Z",
                "metadata": {"supplier_id": "SUP-001", "invoice_id": "S2P-INV-0001"},
            },
            {
                "decision_id": "D-SIM-2",
                "category": "contract_gap",
                "recommended_action": "hold_for_review",
                "created_at": "2026-01-04T00:00:00Z",
                "metadata": {"supplier_id": "SUP-001", "invoice_id": "S2P-INV-9998"},
            },
            {
                "decision_id": "D-SIM-1",
                "category": "contract_gap",
                "recommended_action": "hold_for_review",
                "created_at": "2026-01-03T00:00:00Z",
                "metadata": {"supplier_id": "SUP-001", "invoice_id": "S2P-INV-9999"},
            },
            {
                "decision_id": "D-OTHER",
                "category": "price_variance",
                "recommended_action": "hold_for_review",
                "created_at": "2026-01-05T00:00:00Z",
                "metadata": {"supplier_id": "SUP-001", "invoice_id": "S2P-INV-0002"},
            },
        ]

    def get_all_decisions(self, domain: str):
        assert domain == "s2p"
        return list(self.decisions)

    def get_decision(self, decision_id: str, domain: str | None = None):
        if domain is not None:
            assert domain == self.domain
        for decision in self.decisions:
            if decision["decision_id"] == decision_id:
                return decision
        return None

    def write_decision(
        self,
        domain: str,
        category: str,
        action: str,
        confidence: float,
        factors: dict,
        metadata: dict | None = None,
    ) -> str:
        self.write_calls += 1
        return "D-TEST"

    def write_outcome(
        self,
        decision_id: str,
        actual_action: str,
        is_correct: bool,
        metadata: dict | None = None,
        domain: str | None = None,
    ) -> None:
        raise AssertionError("outcome writes are not part of this double")

    def get_archived_decisions(self, domain: str) -> list[dict]:
        assert domain == self.domain
        return []


class MissingDecisionGraphStore(FakeGraphStore):
    def get_decision(self, decision_id: str, domain: str | None = None):
        return None


class NoGetDecisionGraphStore(FakeGraphStore):
    get_decision = None


class VerifiedDecisionGraphStore(FakeGraphStore):
    def __init__(self) -> None:
        super().__init__()
        for decision in self.decisions:
            if decision["decision_id"] in {"D-TARGET", "D-SIM-1", "D-SIM-2"}:
                decision["verified"] = True


class EmptyEnrichmentGraphStore(FakeGraphStore):
    def read_entity_enrichment(self, *, domain: str, entity_type: str, entity_id: str, namespace: str):
        assert domain == "s2p"
        assert entity_type == "Supplier"
        assert entity_id == "SUP-001"
        assert namespace == "s2p_supplier_metrics"
        return {}


class PersistedEnrichmentGraphStore(FakeGraphStore):
    def read_entity_enrichment(self, *, domain: str, entity_type: str, entity_id: str, namespace: str):
        assert domain == "s2p"
        assert entity_type == "Supplier"
        assert entity_id == "SUP-001"
        assert namespace == "s2p_supplier_metrics"
        return {
            "exception_rate": ProvenancedValue.from_verified(
                0.25,
                source_count=8,
                n_min=5,
                label="computed from 8 verified S2P outcomes",
                computed_at="2026-01-15T00:00:00Z",
            ),
            "avg_lead_time_days": ProvenancedValue.from_fixture(
                12.0,
                label="integration pending",
                computed_at="2026-01-15T00:00:00Z",
            ),
        }


def _known_invoice():
    invoice = find_invoice("S2P-INV-0001")
    assert invoice is not None
    return invoice


def _build(**overrides):
    invoice = _known_invoice()
    context = dict(invoice)
    decision_id = overrides.pop("decision_id", None)
    context.update(overrides.pop("context_data", {}))
    return S2PContextBuilder(**overrides).build_invoice_context(
        invoice_id=invoice["invoice_id"],
        category=invoice["category"],
        decision_id=decision_id,
        context_data=context,
        max_depth=3,
    )


def test_build_invoice_supplier_context_from_fixture_source():
    result = _build()

    supplier = next(node for node in result.nodes if node.type == "supplier")
    assert supplier.source == "fixture"
    assert supplier.properties["source"] == "fixture"
    assert supplier.properties["supplier_id"] == "SUP-001"


def test_po_node_from_invoice_fixture_source():
    result = _build()

    po = next(node for node in result.nodes if node.type == "purchase_order")
    assert po.source == "fixture"
    assert po.properties["po_id"] == "PO-20260001"
    assert po.properties["po_date"]


def test_contract_reference_node_only_when_contract_ref_present():
    result = _build()

    contract = next(node for node in result.nodes if node.type == "contract")
    assert contract.source == "fixture"
    assert contract.properties["contract_ref"] == "CTR-001-CON"


def test_no_fake_contract_terms_when_missing():
    invoice = _known_invoice()
    context = deepcopy(invoice)
    context["metadata"] = {key: value for key, value in context["metadata"].items() if key != "contract_ref"}

    result = S2PContextBuilder().build_invoice_context(
        invoice_id=invoice["invoice_id"],
        category=invoice["category"],
        decision_id=None,
        context_data=context,
    )

    assert not any(node.type == "contract" for node in result.nodes)
    assert any("contract details unavailable" in warning for warning in result.warnings)


def test_similar_decisions_from_graph_store_source():
    result = _build(graph_store=FakeGraphStore(), decision_id="D-TARGET")

    similar = [node for node in result.nodes if node.type == "similar_decision"]
    assert similar
    assert all(node.source == "graph_store" for node in similar)
    assert all(node.properties["provenance_tier"] == "context" for node in similar)
    assert all(node.properties["measured"] is True for node in similar)
    assert all(node.properties["verified"] is False for node in similar)
    assert all("verified decisions" not in node.properties["provenance_label"] for node in similar)


def test_similar_decisions_not_built_from_fixtures():
    result = _build(graph_store=None)

    assert not any(node.type == "similar_decision" for node in result.nodes)


def test_similar_decisions_match_supplier_and_category():
    decisions = S2PContextBuilder(graph_store=FakeGraphStore()).find_similar_decisions(
        supplier_id="SUP-001",
        category="contract_gap",
        decision_id="D-TARGET",
        max_results=5,
    )

    assert {decision["decision_id"] for decision in decisions} == {"D-SIM-1", "D-SIM-2"}


def test_similar_decisions_include_explicit_criteria():
    result = _build(graph_store=FakeGraphStore(), decision_id="D-TARGET")

    similar = [node for node in result.nodes if node.type == "similar_decision"]
    assert similar
    criteria = similar[0].properties["similarity_criteria"]
    assert criteria == {
        "supplier_id": "SUP-001",
        "category": "contract_gap",
        "exclude_decision_id": "D-TARGET",
        "order_by": "created_at DESC",
    }
    assert similar[0].properties["similarity_label"] == "same supplier + same category"
    chain_entry = next(entry for entry in result.evidence_chain if entry["type"] == "similar_decisions")
    assert chain_entry["similarity_criteria"] == criteria


def test_similar_decisions_exclude_target():
    decisions = S2PContextBuilder(graph_store=FakeGraphStore()).find_similar_decisions(
        supplier_id="SUP-001",
        category="contract_gap",
        decision_id="D-TARGET",
        max_results=5,
    )

    assert "D-TARGET" not in {decision["decision_id"] for decision in decisions}


def test_similar_decisions_max_results():
    decisions = S2PContextBuilder(graph_store=FakeGraphStore()).find_similar_decisions(
        supplier_id="SUP-001",
        category="contract_gap",
        decision_id="D-TARGET",
        max_results=1,
    )

    assert len(decisions) == 1
    assert decisions[0]["decision_id"] == "D-SIM-2"


def test_target_decision_node_requires_verified_graph_store_read():
    result = _build(graph_store=MissingDecisionGraphStore(), decision_id="D-MISSING")

    assert not any(
        node.type == "decision" and node.source == "graph_store" and node.id == "D-MISSING"
        for node in result.nodes
    )
    assert any("no GraphStore decision record" in warning for warning in result.warnings)


def test_target_decision_node_uses_graph_store_record_when_found():
    result = S2PContextBuilder(graph_store=FakeGraphStore()).build_invoice_context(
        invoice_id="S2P-INV-0001",
        category="price_variance",
        decision_id="D-TARGET",
        context_data={
            "invoice_id": "S2P-INV-0001",
            "supplier_id": "SUP-001",
            "category": "price_variance",
            "recommended_action": "local_action",
        },
        max_depth=3,
    )

    target = next(node for node in result.nodes if node.type == "decision" and node.id == "D-TARGET")
    assert target.source == "graph_store"
    assert target.properties["category"] == "contract_gap"
    assert target.properties["recommended_action"] == "escalate_to_buyer"
    assert target.properties["provenance_tier"] == "context"
    assert target.properties["measured"] is True
    assert target.properties["verified"] is False


def test_graph_store_unverified_decision_does_not_claim_verified():
    result = _build(graph_store=FakeGraphStore(), decision_id="D-TARGET")

    target = next(node for node in result.nodes if node.type == "decision" and node.id == "D-TARGET")
    assert target.properties["source"] == "graph_store"
    assert target.properties["verified"] is False
    assert target.properties["provenance_tier"] == "context"
    assert "verified decisions" not in target.properties["provenance_label"]


def test_graph_store_verified_decision_can_claim_verified():
    result = _build(graph_store=VerifiedDecisionGraphStore(), decision_id="D-TARGET")

    target = next(node for node in result.nodes if node.type == "decision" and node.id == "D-TARGET")
    assert target.properties["source"] == "graph_store"
    assert target.properties["verified"] is True
    assert target.properties["provenance_tier"] == "learned"
    assert "verified" in target.properties["provenance_label"]


def test_similar_decision_evidence_chain_distinguishes_graphstore_read_from_verified():
    unverified = _build(graph_store=FakeGraphStore(), decision_id="D-TARGET")
    unverified_entry = next(entry for entry in unverified.evidence_chain if entry["type"] == "similar_decisions")
    assert unverified_entry["provenance_label"] == "decision history · GraphStore read"
    assert unverified_entry["verified_count"] == 0
    assert unverified_entry["unverified_count"] == 2
    assert unverified_entry["verified"] is False

    verified = _build(graph_store=VerifiedDecisionGraphStore(), decision_id="D-TARGET")
    verified_entry = next(entry for entry in verified.evidence_chain if entry["type"] == "similar_decisions")
    assert "verified" in verified_entry["provenance_label"]
    assert verified_entry["verified_count"] == 2
    assert verified_entry["unverified_count"] == 0
    assert verified_entry["verified"] is True
    assert verified_entry["provenance_tier"] == "learned"


def test_no_graph_store_source_from_context_only():
    result = S2PContextBuilder(graph_store=NoGetDecisionGraphStore()).build_invoice_context(
        invoice_id="S2P-INV-0001",
        category="contract_gap",
        decision_id="D-CONTEXT-ONLY",
        context_data={
            "invoice_id": "S2P-INV-0001",
            "supplier_id": "SUP-001",
            "category": "contract_gap",
            "recommended_action": "hold_for_review",
        },
        max_depth=3,
    )

    assert not any(
        node.type == "decision" and node.source == "graph_store" and node.id == "D-CONTEXT-ONLY"
        for node in result.nodes
    )
    assert any("no GraphStore decision record" in warning for warning in result.warnings)


def test_centroid_context_from_injected_scorer_source():
    result = _build(scorer=FakeScorer())

    centroid = next(node for node in result.nodes if node.type == "centroid")
    assert centroid.source == "scorer"
    assert centroid.properties["source"] == "scorer"
    assert centroid.properties["provenance_tier"] == "learned"
    assert centroid.properties["integration_status"] == "configured"
    assert centroid.properties["measured"] is True
    assert centroid.properties["factor_names"] == S2PDomainConfig.factors


def test_no_l5_centroid_read_or_scorer_instantiation():
    scorer = FakeScorer()
    result = _build(scorer=scorer)

    assert any(node.type == "centroid" for node in result.nodes)
    assert scorer.instantiated is False


def test_centroid_unavailable_warning():
    result = _build(scorer=None)

    assert not any(node.type == "centroid" for node in result.nodes)
    assert any("centroid context unavailable" in warning for warning in result.warnings)


def test_each_node_has_source_field():
    result = _build(scorer=FakeScorer(), graph_store=FakeGraphStore(), decision_id="D-TARGET")

    assert result.nodes
    assert all(node.source in {"fixture", "graph_store", "scorer"} for node in result.nodes)
    assert all(node.properties.get("source") in {"fixture", "graph_store", "scorer"} for node in result.nodes)
    for node in result.nodes:
        assert node.properties["provenance_label"]
        assert node.properties["provenance_tier"] in {"learned", "context", "unavailable"}
        assert node.properties["integration_status"] in {"configured", "pending", "not_applicable"}
        assert isinstance(node.properties["measured"], bool)
        assert node.properties["display_prefix"]


def test_every_evidence_chain_entry_has_provenance():
    result = _build(scorer=FakeScorer(), graph_store=FakeGraphStore(), decision_id="D-TARGET")

    assert result.evidence_chain
    for entry in result.evidence_chain:
        assert entry["source"] in {"fixture", "graph_store", "scorer", "unavailable"}
        assert entry["provenance_label"]
        assert entry["provenance_tier"] in {"learned", "context", "unavailable"}
        assert isinstance(entry["measured"], bool)


def test_fixture_supplier_values_are_integration_pending_context():
    result = _build()

    supplier = next(node for node in result.nodes if node.type == "supplier")
    assert supplier.properties["source"] == "fixture"
    assert supplier.properties["provenance_label"] == "supplier context · integration pending"
    assert supplier.properties["provenance_tier"] == "context"
    assert supplier.properties["integration_status"] == "pending"
    assert supplier.properties["measured"] is False
    assert supplier.properties["exception_rate"] is not None


def test_supplier_node_no_enrichment_key_when_store_empty():
    result = _build(graph_store=EmptyEnrichmentGraphStore())

    supplier = next(node for node in result.nodes if node.type == "supplier")
    assert supplier.source == "fixture"
    assert supplier.properties["source"] == "fixture"
    assert supplier.properties["supplier_id"] == "SUP-001"
    assert supplier.properties["provenance_label"] == "supplier context · integration pending"
    assert supplier.properties["provenance_tier"] == "context"
    assert supplier.properties["integration_status"] == "pending"
    assert supplier.properties["measured"] is False
    assert supplier.properties["exception_rate"] is not None
    assert "enrichment" not in supplier.properties


def test_supplier_node_enrichment_structurally_separated():
    result = _build(graph_store=PersistedEnrichmentGraphStore())

    supplier = next(node for node in result.nodes if node.type == "supplier")
    assert supplier.source == "fixture"
    assert supplier.properties["source"] == "fixture"
    assert supplier.properties["supplier_id"] == "SUP-001"
    assert supplier.properties["provenance_tier"] == "context"
    assert supplier.properties["measured"] is False
    assert supplier.properties.get("verified") in (None, False)

    enrichment = supplier.properties["enrichment"]
    assert set(enrichment) == {"avg_lead_time_days", "exception_rate"}
    assert "supplier_id" not in enrichment
    assert supplier.properties["exception_rate"] != enrichment["exception_rate"]

    for metric in enrichment.values():
        assert isinstance(metric, dict)
        assert {"source", "provenance_tier", "measured", "verified", "provenance_label"}.issubset(metric)

    assert enrichment["exception_rate"]["source"] == "verified_outcomes"
    assert enrichment["exception_rate"]["provenance_tier"] == "learned"
    assert enrichment["exception_rate"]["measured"] is True
    assert enrichment["exception_rate"]["verified"] is True
    assert enrichment["avg_lead_time_days"]["source"] == "fixture"
    assert enrichment["avg_lead_time_days"]["provenance_tier"] == "context"
    assert enrichment["avg_lead_time_days"]["measured"] is False
    assert enrichment["avg_lead_time_days"]["verified"] is False


def test_no_unlabeled_fixture_metric():
    result = _build()

    metric_keys = {"otif_score", "exception_rate", "recent_trend", "contractual_lead_time_days"}
    for node in result.nodes:
        if node.source != "fixture":
            continue
        if metric_keys.intersection(node.properties):
            assert node.properties["provenance_label"]
            assert node.properties["provenance_tier"] == "context"
            assert node.properties["integration_status"] == "pending"
            assert node.properties["measured"] is False


def test_missing_supplier_safe_warning():
    result = S2PContextBuilder().build_invoice_context(
        invoice_id=None,
        category="price_variance",
        decision_id=None,
        context_data={"category": "price_variance"},
    )

    assert any("supplier unavailable" in warning for warning in result.warnings)


def test_no_mutation_of_inputs():
    invoice = _known_invoice()
    context = deepcopy(invoice)
    before = deepcopy(context)

    S2PContextBuilder(scorer=FakeScorer(), graph_store=FakeGraphStore()).build_invoice_context(
        invoice_id=invoice["invoice_id"],
        category=invoice["category"],
        decision_id="D-TARGET",
        context_data=context,
    )

    assert context == before
