from __future__ import annotations

from copy import deepcopy

from copilot_sdk.situation import SituationAnalyzer, SituationContext, TraversalEdge, TraversalNode

from app.services.s2p_situation_pattern import S2PInvoiceTraversalPattern


class FakeStore:
    domain = "s2p"

    def __init__(self) -> None:
        self.write_calls = 0
        self.decisions = {
            "D-1": {
                "decision_id": "D-1",
                "category": "price_variance",
                "confidence": 0.91,
                "recommended_action": "hold_for_review",
                "metadata": {
                    "invoice_id": "S2P-INV-0001",
                    "supplier_id": "SUP-001",
                    "supplier_name": "Aster",
                    "po_number": "PO-1",
                },
                "factors": {"amount_variance_ratio": 0.2},
            }
        }
        self.links = [
            {
                "decision_id": "D-1",
                "entity_id": "S2P-INV-0001",
                "edge_type": "DECIDED_ON",
            }
        ]

    def get_decision(self, decision_id: str):
        return self.decisions.get(decision_id)

    def get_decision_links(self, decision_id: str | None = None):
        if decision_id is None:
            return list(self.links)
        return [link for link in self.links if link["decision_id"] == decision_id]

    def get_all_decisions(self, domain: str):
        assert domain == "s2p"
        return list(self.decisions.values())

    def query_context(self, invoice_id: str, max_depth: int):
        return {"neighbors": [{"node": {"invoice_id": invoice_id}, "max_depth": max_depth}]}

    def write_decision(self, *_args, **_kwargs):
        self.write_calls += 1


class FakeScorer:
    def get_centroid(self, category: str, action: str):
        return [0.5] * 7


def _intent(**scope):
    return SituationAnalyzer().normalize_signal(
        {
            "domain": "s2p",
            "intent_type": "evidence",
            "verb": "explain",
            "subject": "invoice",
            "scope": scope or {"invoice_id": "S2P-INV-0001"},
        }
    )


def _raw_intent(payload):
    return SituationAnalyzer().normalize_signal(payload)


def test_supports_s2p_invoice_intent() -> None:
    assert S2PInvoiceTraversalPattern().supports(_intent(invoice_id="S2P-INV-0001"))


def test_rejects_non_s2p_intent() -> None:
    intent = SituationAnalyzer().normalize_signal(
        {"domain": "soc", "signal_type": "alert", "subject": "alert", "scope": {"alert_id": "A-1"}}
    )

    assert not S2PInvoiceTraversalPattern().supports(intent)


def test_builds_context_with_invoice_decision_supplier_nodes() -> None:
    context = S2PInvoiceTraversalPattern().traverse(
        _intent(invoice_id="S2P-INV-0001", decision_id="D-1"),
        graph_store=FakeStore(),
    )

    assert isinstance(context, SituationContext)
    assert any(isinstance(node, TraversalNode) and node.type == "invoice" for node in context.nodes)
    assert any(node.type == "decision" for node in context.nodes)
    assert any(node.type == "supplier" for node in context.nodes)
    assert any(isinstance(edge, TraversalEdge) and edge.type == "DECIDED_ON" for edge in context.edges)


def test_missing_store_returns_warning_context_not_crash() -> None:
    context = S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"), graph_store=None)

    assert context.nodes
    assert context.warnings


def test_bounded_max_depth_honored() -> None:
    context = S2PInvoiceTraversalPattern().traverse(
        _intent(invoice_id="S2P-INV-0001", decision_id="D-1"),
        graph_store=FakeStore(),
        max_depth=1,
    )

    assert context.max_depth == 1
    assert not any(node.depth > 1 for node in context.nodes)


def test_no_mutation_calls_on_fake_store() -> None:
    store = FakeStore()

    S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"), graph_store=store)

    assert store.write_calls == 0


def test_evidence_chain_populated_with_context() -> None:
    context = S2PInvoiceTraversalPattern().traverse(
        _intent(invoice_id="S2P-INV-0001", decision_id="D-1"),
        graph_store=FakeStore(),
    )

    assert context.evidence_chain
    assert context.metadata["context_used"]["category"]


def test_no_live_age_required() -> None:
    context = S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"))

    assert context.domain == "s2p"


def test_uses_p33_types() -> None:
    analyzer = SituationAnalyzer([S2PInvoiceTraversalPattern()])
    context = analyzer.analyze_intent(_intent(invoice_id="S2P-INV-0001"), graph_store=FakeStore())

    assert isinstance(context, SituationContext)
    assert all(isinstance(node, TraversalNode) for node in context.nodes)
    assert all(isinstance(edge, TraversalEdge) for edge in context.edges)


def test_does_not_require_graphstore_traverse() -> None:
    store = FakeStore()

    assert not hasattr(store, "traverse")
    context = S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"), graph_store=store)

    assert context.nodes


def test_supports_invoice_id_from_intent_metadata() -> None:
    intent = _raw_intent(
        {
            "domain": "s2p",
            "intent_type": "evidence",
            "verb": "explain",
            "subject": "event",
            "metadata": {"invoice_id": "S2P-INV-0001"},
        }
    )

    assert S2PInvoiceTraversalPattern().supports(intent)


def test_supports_invoice_id_from_raw_payload() -> None:
    intent = _raw_intent(
        {
            "domain": "s2p",
            "intent_type": "evidence",
            "verb": "explain",
            "subject": "event",
            "invoice_id": "S2P-INV-0001",
        }
    )

    assert S2PInvoiceTraversalPattern().supports(intent)


def test_traverse_builds_invoice_node_from_raw_payload_invoice_id() -> None:
    intent = _raw_intent(
        {
            "domain": "s2p",
            "intent_type": "evidence",
            "verb": "explain",
            "subject": "event",
            "invoice_id": "S2P-INV-0001",
        }
    )

    context = S2PInvoiceTraversalPattern().traverse(intent, graph_store=None)

    assert any(node.type == "invoice" and node.id == "S2P-INV-0001" for node in context.nodes)


def test_scope_decision_id_overrides_raw_payload_decision_id() -> None:
    intent = _raw_intent(
        {
            "domain": "s2p",
            "intent_type": "evidence",
            "verb": "explain",
            "subject": "event",
            "decision_id": "D-RAW",
            "scope": {"invoice_id": "S2P-INV-0001", "decision_id": "D-1"},
        }
    )

    context = S2PInvoiceTraversalPattern().traverse(intent, graph_store=FakeStore())

    assert context.decision_id == "D-1"
    assert any(node.type == "decision" and node.id == "D-1" for node in context.nodes)


def test_supplier_name_from_metadata_appears_in_context_node() -> None:
    intent = _raw_intent(
        {
            "domain": "s2p",
            "intent_type": "evidence",
            "verb": "explain",
            "subject": "event",
            "metadata": {
                "invoice_id": "RAW-INV-1",
                "supplier_id": "SUP-META",
                "supplier_name": "Meta Supplier",
            },
        }
    )

    context = S2PInvoiceTraversalPattern().traverse(intent, graph_store=None)

    supplier_nodes = [node for node in context.nodes if node.type == "supplier"]
    assert supplier_nodes
    assert supplier_nodes[0].properties["supplier_name"] == "Meta Supplier"


def test_intent_metadata_and_raw_payload_not_mutated() -> None:
    raw = {
        "domain": "s2p",
        "intent_type": "evidence",
        "verb": "explain",
        "subject": "event",
        "invoice_id": "S2P-INV-0001",
        "metadata": {"supplier_name": "Aster"},
    }
    original = deepcopy(raw)
    intent = _raw_intent(raw)
    metadata_before = deepcopy(intent.metadata)

    S2PInvoiceTraversalPattern().traverse(intent, graph_store=None)

    assert raw == original
    assert intent.metadata == metadata_before


def test_s2p_pattern_uses_context_builder_for_known_invoice() -> None:
    context = S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"), graph_store=FakeStore())

    assert context.metadata["p38_context_builder"] is True
    assert "fixture" in context.metadata["data_sources"]


def test_s2p_pattern_outputs_supplier_node_and_edge() -> None:
    context = S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"), graph_store=FakeStore())

    assert any(node.type == "supplier" and node.source == "fixture" for node in context.nodes)
    assert any(edge.type == "FROM_SUPPLIER" for edge in context.edges)


def test_s2p_pattern_outputs_po_node_when_available() -> None:
    context = S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"), graph_store=FakeStore())

    assert any(node.type == "purchase_order" and node.source == "fixture" for node in context.nodes)


def test_s2p_pattern_contract_warning_when_terms_missing() -> None:
    context = S2PInvoiceTraversalPattern().traverse(_intent(invoice_id="S2P-INV-0001"), graph_store=FakeStore())

    assert any(node.type == "contract" for node in context.nodes)
    assert any("contract details unavailable" in warning for warning in context.warnings)


def test_s2p_pattern_centroid_node_when_scorer_available() -> None:
    context = S2PInvoiceTraversalPattern(scorer=FakeScorer()).traverse(
        _intent(invoice_id="S2P-INV-0001"),
        graph_store=FakeStore(),
    )

    assert any(node.type == "centroid" and node.source == "scorer" for node in context.nodes)


def test_s2p_pattern_preserves_p35_raw_payload_extraction() -> None:
    intent = _raw_intent(
        {
            "domain": "s2p",
            "intent_type": "evidence",
            "verb": "explain",
            "subject": "event",
            "invoice_id": "S2P-INV-0001",
        }
    )

    context = S2PInvoiceTraversalPattern().traverse(intent, graph_store=FakeStore())

    assert any(node.type == "invoice" and node.id == "S2P-INV-0001" for node in context.nodes)


def test_s2p_pattern_preserves_no_mutation() -> None:
    raw = {
        "domain": "s2p",
        "intent_type": "evidence",
        "verb": "explain",
        "subject": "event",
        "invoice_id": "S2P-INV-0001",
        "metadata": {"supplier_name": "Aster"},
    }
    original = deepcopy(raw)
    intent = _raw_intent(raw)

    S2PInvoiceTraversalPattern().traverse(intent, graph_store=FakeStore())

    assert raw == original


def test_s2p_pattern_keeps_degraded_warning_for_missing_context() -> None:
    context = S2PInvoiceTraversalPattern().traverse(_intent(category="price_variance"), graph_store=None)

    assert context.warnings
    assert context.metadata["degraded"] is True
