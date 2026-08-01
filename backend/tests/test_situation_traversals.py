from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot_sdk.graph import InMemoryGraphStore
from copilot_sdk.situation import ContextChain, SituationAnalyzer, TraversalPattern

from app.domains.s2p.config import S2PDomainConfig
from app.routers.s2p_situation import router as situation_router
from app.services.situation_traversals import (
    ContractGapTraversal,
    DuplicateRiskTraversal,
    FormatComplianceTraversal,
    PriceVarianceTraversal,
    QuantityMismatchTraversal,
    S2P_TRAVERSAL_PATTERNS,
    SITUATION_NL_TEMPLATES,
    build_context_chain,
)


class ScorerWithWeights:
    def __init__(self, graph_store: InMemoryGraphStore) -> None:
        self.graph_store = graph_store

    def get_dk_weights(self):
        return [[1.0 / len(S2PDomainConfig.factors)] * len(S2PDomainConfig.factors)]


def _store(category: str = "price_variance", confidence: float = 0.91) -> tuple[InMemoryGraphStore, str]:
    store = InMemoryGraphStore(domain="s2p")
    factors = {name: 0.2 for name in S2PDomainConfig.factors}
    factors.update(
        {
            "amount_variance_ratio": 0.052,
            "commodity_index_correlation": 0.048,
            "duplicate_score": 0.82,
            "match_status": 0.7,
            "tax_regulatory_compliance": 0.4,
        }
    )
    decision_id = store.write_decision(
        "s2p",
        category,
        "hold_for_review",
        confidence,
        factors,
        metadata={
            "decision_id": "D-1",
            "entity_id": "S2P-INV-0001",
            "invoice_id": "S2P-INV-0001",
            "supplier_id": "SUP-001",
            "supplier_name": "Aster",
            "amount": 100.0,
            "po_id": "PO-1",
            "po_number": "PO-1",
            "contract_ref": "CTR-1",
            "commodity": "Copper",
            "lookback": 30,
            "threshold": 10.0,
            "line_items": [{"quantity": 10}],
            "gr_qty": 9,
            "provenance": "fixture",
        },
    )
    store.link_decision_to_entity(decision_id, "S2P-INV-0001", domain="s2p")
    similar_id = store.write_decision(
        "s2p",
        category,
        "hold_for_review",
        0.73,
        factors,
        metadata={
            "decision_id": "D-2",
            "entity_id": "S2P-INV-0002",
            "invoice_id": "S2P-INV-0002",
            "supplier_id": "SUP-001",
            "amount": 100.0,
            "provenance": "fixture",
        },
    )
    store.link_decision_to_entity(similar_id, "S2P-INV-0002", domain="s2p")
    return store, decision_id


def _context(pattern, store: InMemoryGraphStore, decision_id: str, max_depth: int = 3):
    analyzer = SituationAnalyzer([pattern], max_allowed_depth=3)
    intent = analyzer.normalize_signal(
        {
            "domain": "s2p",
            "intent_type": "situation_context",
            "verb": "explain",
            "subject": "decision",
            "decision_id": decision_id,
            "scope": {"decision_id": decision_id, "category": pattern.category},
        }
    )
    return analyzer.analyze_intent(intent, graph_store=store, max_depth=max_depth)


def _client(store: InMemoryGraphStore) -> TestClient:
    app = FastAPI()
    app.state.graph_store = store
    app.state.scorer = ScorerWithWeights(store)
    app.include_router(situation_router)
    return TestClient(app)


def _client_without_store() -> TestClient:
    app = FastAPI()
    app.include_router(situation_router)
    return TestClient(app)


class GraphRichStore(InMemoryGraphStore):
    def query_context(
        self, entity_id: str, max_depth: int, domain: str | None = None
    ):
        return [
            {
                "node": "commodity_index",
                "id": "commodity:Copper",
                "depth": 1,
                "properties": {"commodity": "Copper", "provenance": "graph_store"},
            }
        ]


class GraphCompleteStore(InMemoryGraphStore):
    def query_context(
        self, entity_id: str, max_depth: int, domain: str | None = None
    ):
        return [
            {
                "node": "commodity_index",
                "id": "commodity:Copper",
                "depth": 1,
                "properties": {"commodity": "Copper", "provenance": "graph_store"},
            },
            {
                "node": "contract_clause",
                "id": "contract_clause:CTR-1",
                "depth": 2,
                "properties": {"ref": "CTR-1", "provenance": "graph_store"},
            },
            {
                "node": "threshold",
                "id": "threshold:10.0",
                "depth": 3,
                "properties": {"threshold_pct": 10.0, "provenance": "graph_store"},
            },
        ]


def test_price_variance_traversal_with_fixture_data() -> None:
    store, decision_id = _store("price_variance")
    context = _context(PriceVarianceTraversal(), store, decision_id)

    assert [node.type for node in context.nodes] == [
        "invoice",
        "commodity_index",
        "contract_clause",
        "threshold",
    ]
    assert context.metadata["template_variables"]["commodity"] == "Copper"


def test_quantity_mismatch_traversal_with_fixture_data() -> None:
    store, decision_id = _store("quantity_mismatch")
    context = _context(QuantityMismatchTraversal(), store, decision_id)

    assert any(node.type == "goods_receipt" for node in context.nodes)
    assert "delta" in context.metadata["template_variables"]


def test_duplicate_risk_traversal_with_fixture_data() -> None:
    store, decision_id = _store("duplicate_risk")
    context = _context(DuplicateRiskTraversal(), store, decision_id)

    assert any(node.type == "similar_invoice" for node in context.nodes)
    assert context.metadata["template_variables"]["similarity_pct"] >= 0


def test_contract_gap_traversal_with_fixture_data() -> None:
    store, decision_id = _store("contract_gap")
    context = _context(ContractGapTraversal(), store, decision_id)

    assert any(node.type == "coverage" for node in context.nodes)
    assert context.metadata["template_variables"]["ref"] == "CTR-1"


def test_format_compliance_traversal_with_fixture_data() -> None:
    store, decision_id = _store("format_compliance")
    context = _context(FormatComplianceTraversal(), store, decision_id)

    assert any(node.type == "rules" for node in context.nodes)
    assert "historical_pct" in context.metadata["template_variables"]


def test_price_variance_missing_graph_data_graceful() -> None:
    store, decision_id = _store("price_variance")
    decision = store.get_decision(decision_id, domain="s2p")
    assert decision is not None
    decision["metadata"].pop("commodity", None)
    store._decisions[decision_id] = decision

    context = _context(PriceVarianceTraversal(), store, decision_id)

    assert "Commodity data unavailable" in context.warnings
    assert context.metadata["context_available"] is False


def test_quantity_mismatch_missing_gr_graceful() -> None:
    store, decision_id = _store("quantity_mismatch")
    decision = store.get_decision(decision_id, domain="s2p")
    assert decision is not None
    decision["metadata"].pop("gr_qty", None)
    store._decisions[decision_id] = decision

    context = _context(QuantityMismatchTraversal(), store, decision_id)

    assert "GR data pending" in context.warnings


def test_duplicate_missing_similar_data_graceful() -> None:
    store, decision_id = _store("duplicate_risk")
    store._decisions = {decision_id: store._decisions[decision_id]}

    context = _context(DuplicateRiskTraversal(), store, decision_id)

    assert any(node.type == "similar_invoice" for node in context.nodes)


def test_contract_gap_missing_contract_graceful() -> None:
    store, decision_id = _store("contract_gap")
    decision = store.get_decision(decision_id, domain="s2p")
    assert decision is not None
    decision["metadata"].pop("contract_ref", None)
    store._decisions[decision_id] = decision

    context = _context(ContractGapTraversal(), store, decision_id)

    assert "No contract clause found" in context.warnings


def test_format_compliance_missing_data_graceful() -> None:
    store, decision_id = _store("format_compliance")

    context = _context(FormatComplianceTraversal(), store, decision_id)

    assert context.metadata["context_available"] is True


def test_endpoint_returns_200_with_valid_decision_id() -> None:
    store, decision_id = _store("price_variance")

    response = _client(store).get(f"/api/s2p/situation/{decision_id}")

    assert response.status_code == 200
    assert response.json()["decision_id"] == decision_id


def test_endpoint_returns_404_for_unknown_decision_id() -> None:
    store, _decision_id = _store("price_variance")

    response = _client(store).get("/api/s2p/situation/UNKNOWN")

    assert response.status_code == 404


def test_endpoint_returns_503_when_graph_store_unavailable() -> None:
    response = _client_without_store().get("/api/s2p/situation/D-1")

    assert response.status_code == 503


def test_endpoint_returns_400_for_unsupported_category() -> None:
    store, decision_id = _store("unsupported")

    response = _client(store).get(f"/api/s2p/situation/{decision_id}")

    assert response.status_code == 400


def test_nl_explanation_contains_expected_template_variables() -> None:
    store, decision_id = _store("price_variance")

    payload = _client(store).get(f"/api/s2p/situation/{decision_id}").json()

    assert "5.2% price delta" in payload["nl_explanation"]
    assert "Copper moved 4.8%" in payload["nl_explanation"]


def test_confidence_comes_from_decision_not_factor_preview() -> None:
    store, decision_id = _store("price_variance", confidence=0.41)

    payload = _client(store).get(f"/api/s2p/situation/{decision_id}").json()

    assert payload["confidence"] == 0.41
    assert "Confidence: 41%." in payload["nl_explanation"]


def test_response_shape_matches_specification() -> None:
    store, decision_id = _store("price_variance")

    payload = _client(store).get(f"/api/s2p/situation/{decision_id}").json()

    assert {
        "decision_id",
        "category",
        "context_chain",
        "nl_explanation",
        "confidence",
        "factors_used",
        "traversal_depth",
        "missing_variables",
        "situation_context",
        "provenance",
    }.issubset(payload)


def test_max_depth_parameter_limits_traversal() -> None:
    store, decision_id = _store("price_variance")

    payload = _client(store).get(f"/api/s2p/situation/{decision_id}?max_depth=1").json()

    assert payload["traversal_depth"] <= 1


def test_endpoint_is_read_only() -> None:
    store, decision_id = _store("price_variance")
    decision_count_before = store.count_decisions("s2p")
    link_count_before = len(store.get_decision_links(domain="s2p"))

    response = _client(store).get(f"/api/s2p/situation/{decision_id}")

    assert response.status_code == 200
    assert store.count_decisions("s2p") == decision_count_before
    assert len(store.get_decision_links(domain="s2p")) == link_count_before


def test_traversal_uses_graph_context_before_fixture_node() -> None:
    store = GraphRichStore(domain="s2p")
    base_store, decision_id = _store("price_variance")
    store._decisions = base_store._decisions
    store._edges = base_store._edges

    context = _context(PriceVarianceTraversal(), store, decision_id)

    commodity_nodes = [node for node in context.nodes if node.type == "commodity_index"]
    assert commodity_nodes
    assert commodity_nodes[0].source == "graph_store"


def test_no_merge_in_situation_templates_or_traversals() -> None:
    haystack = "\n".join([*SITUATION_NL_TEMPLATES.values(), repr(S2P_TRAVERSAL_PATTERNS)])

    assert "MERGE" not in haystack


def test_factor_count_matches_domain_config_not_hardcoded() -> None:
    store, decision_id = _store("price_variance")
    context = _context(PriceVarianceTraversal(), store, decision_id)

    assert context.metadata["factor_count"] == len(S2PDomainConfig.factors)


def test_provenance_on_fixture_nodes() -> None:
    store, decision_id = _store("price_variance")
    context = _context(PriceVarianceTraversal(), store, decision_id)

    assert all(node.properties.get("provenance") for node in context.nodes)


def test_context_chain_nodes_include_provenance_field() -> None:
    store, decision_id = _store("price_variance")

    payload = _client(store).get(f"/api/s2p/situation/{decision_id}").json()

    assert payload["context_chain"]
    assert all("provenance" in node for node in payload["context_chain"])


def test_all_graph_sourced_nodes_report_context_overall() -> None:
    store = GraphCompleteStore(domain="s2p")
    base_store, decision_id = _store("price_variance")
    decision = base_store.get_decision(decision_id, domain="s2p")
    assert decision is not None
    decision["metadata"]["provenance"] = "graph_store"
    base_store._decisions[decision_id] = decision
    store._decisions = base_store._decisions
    store._edges = base_store._edges

    payload = _client(store).get(f"/api/s2p/situation/{decision_id}").json()

    assert payload["provenance"]["overall"] == "context"
    assert {node["provenance"] for node in payload["context_chain"]} == {"context"}


def test_fixture_synthesized_node_reports_sample_overall() -> None:
    store, decision_id = _store("price_variance")

    payload = _client(store).get(f"/api/s2p/situation/{decision_id}").json()

    assert any(node["provenance"] == "sample" for node in payload["context_chain"])
    assert payload["provenance"]["overall"] == "sample"


def test_all_category_patterns_satisfy_protocol_and_context_chain() -> None:
    store, decision_id = _store("price_variance")

    for pattern in S2P_TRAVERSAL_PATTERNS:
        assert isinstance(pattern, TraversalPattern)
    context = _context(PriceVarianceTraversal(), store, decision_id)
    chain = build_context_chain(context, "example")
    assert isinstance(chain, ContextChain)
