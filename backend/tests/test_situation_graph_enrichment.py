from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from copilot_sdk.graph import InMemoryGraphStore

from app.graph_contract import S2P_GRAPH_CONTRACT
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.routers.s2p_enrichment_context import router as enrichment_context_router
from app.routers.s2p_situation import router as situation_router
from app.services import situation_graph_enrichment
from app.services.situation_graph_enrichment import (
    NAMESPACE,
    S2PSituationEnricher,
)
from app.services.situation_traversals import (
    ContractGapTraversal,
    FormatComplianceTraversal,
    PriceVarianceTraversal,
    QuantityMismatchTraversal,
)
from tests.test_situation_traversals import ScorerWithWeights, _context, _store


def _node_names() -> set[str]:
    return {getattr(node_type, "label", "") for node_type in S2P_GRAPH_CONTRACT.node_types}


def _edge_names() -> set[str]:
    return {getattr(edge_type, "label", "") for edge_type in S2P_GRAPH_CONTRACT.edge_types}


def _client(store: InMemoryGraphStore) -> TestClient:
    app = FastAPI()
    app.state.graph_store = store
    app.state.scorer = ScorerWithWeights(store)
    app.include_router(enrichment_context_router)
    app.include_router(situation_router)
    return TestClient(app)


def _price_store(provenance: str = "graph_store") -> tuple[InMemoryGraphStore, str]:
    store, decision_id = _store("price_variance")
    decision = store.get_decision(decision_id, domain="s2p")
    assert decision is not None
    decision["metadata"]["provenance"] = provenance
    store._decisions[decision_id] = decision
    return store, decision_id


def test_contract_includes_commodity_index_node_type() -> None:
    assert "CommodityIndex" in _node_names()


def test_contract_includes_contract_clause_node_type() -> None:
    assert "ContractClause" in _node_names()


def test_contract_includes_has_commodity_index_edge_type() -> None:
    assert "HAS_COMMODITY_INDEX" in _edge_names()


def test_enrich_invoice_context_creates_commodity_index_node() -> None:
    store, _decision_id = _price_store()

    written = S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "commodity_index", "properties": {"commodity": "Copper", "delta_pct": 4.8}},
    )

    assert written == 1
    assert store.read_entity_enrichment(
        domain="s2p",
        entity_type="CommodityIndex",
        entity_id="CommodityIndex:Copper",
        namespace=NAMESPACE,
    )


def test_enrich_invoice_context_creates_contract_clause_node() -> None:
    store, _decision_id = _price_store()

    written = S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "contract_clause", "properties": {"ref": "CTR-1", "threshold_pct": 10.0}},
    )

    assert written == 1
    assert store.read_entity_enrichment(
        domain="s2p",
        entity_type="ContractClause",
        entity_id="ContractClause:CTR-1",
        namespace=NAMESPACE,
    )


def test_enrich_invoice_context_creates_goods_receipt_node() -> None:
    store, _decision_id = _price_store()

    written = S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "goods_receipt", "properties": {"gr_id": "GR-1", "qty_received": 9}},
    )

    assert written == 1
    assert store.read_entity_enrichment(
        domain="s2p",
        entity_type="GoodsReceipt",
        entity_id="GoodsReceipt:GR-1",
        namespace=NAMESPACE,
    )


def test_duplicate_enrichment_is_idempotent() -> None:
    store, _decision_id = _price_store()
    context = {"node_type": "commodity_index", "properties": {"commodity": "Copper", "delta_pct": 4.8}}

    first = S2PSituationEnricher(store).enrich_invoice_context("S2P-INV-0001", context)
    second = S2PSituationEnricher(store).enrich_invoice_context("S2P-INV-0001", context)

    assert first == 1
    assert second == 0
    links = [
        link
        for link in store.get_decision_links(domain="s2p")
        if link["entity_id"] == "CommodityIndex:Copper" and link["edge_type"] == "HAS_COMMODITY_INDEX"
    ]
    assert len(links) == 1


def test_all_enriched_nodes_have_enriched_provenance() -> None:
    store, _decision_id = _price_store()
    S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
    )

    metrics = store.read_entity_enrichment(
        domain="s2p",
        entity_type="CommodityIndex",
        entity_id="CommodityIndex:Copper",
        namespace=NAMESPACE,
    )

    assert metrics["provenance"].value == "enriched"


def test_no_merge_in_situation_enrichment_code() -> None:
    source = inspect.getsource(situation_graph_enrichment)

    assert "MERGE" not in source


def test_no_raw_params_in_situation_enrichment_code() -> None:
    source = inspect.getsource(situation_graph_enrichment)

    assert "$" not in source


def test_does_not_import_legacy_graph_py() -> None:
    source = inspect.getsource(situation_graph_enrichment)

    assert "app.domains.s2p.graph" not in source
    assert "legacy graph" not in source.lower()


def test_traversal_uses_enriched_graph_node_after_enrichment() -> None:
    store, decision_id = _price_store()
    S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "commodity_index", "properties": {"commodity": "Copper", "delta_pct": 9.9}},
    )

    context = _context(PriceVarianceTraversal(), store, decision_id, max_depth=1)

    commodity = [node for node in context.nodes if node.type == "commodity_index"][0]
    assert commodity.properties["commodity"] == "Copper"
    assert commodity.properties["delta_pct"] == 9.9


def test_enriched_graph_node_provenance_is_enriched() -> None:
    store, decision_id = _price_store()
    S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
    )

    context = _context(PriceVarianceTraversal(), store, decision_id, max_depth=1)

    commodity = [node for node in context.nodes if node.type == "commodity_index"][0]
    assert commodity.properties["provenance"] == "enriched"


def test_contract_gap_uses_enriched_contract_clause_node() -> None:
    store, decision_id = _store("contract_gap")
    S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "contract_clause", "properties": {"ref": "CTR-1", "threshold_pct": 10.0}},
    )

    context = _context(ContractGapTraversal(), store, decision_id, max_depth=2)

    contract = [node for node in context.nodes if node.type == "contract"][0]
    assert contract.properties["provenance"] == "enriched"
    assert contract.properties["threshold_pct"] == 10.0


def test_quantity_mismatch_uses_enriched_goods_receipt_node() -> None:
    store, decision_id = _store("quantity_mismatch")
    S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "goods_receipt", "properties": {"gr_id": "GR-1", "qty_received": 9}},
    )

    context = _context(QuantityMismatchTraversal(), store, decision_id, max_depth=2)

    goods_receipt = [node for node in context.nodes if node.type == "goods_receipt"][0]
    assert goods_receipt.properties["provenance"] == "enriched"
    assert goods_receipt.properties["qty_received"] == 9


def test_format_compliance_uses_enriched_compliance_history_node() -> None:
    store, decision_id = _store("format_compliance")
    S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "historical_compliance", "properties": {"rule_id": "FMT-1", "pass_rate": 0.96}},
    )

    context = _context(FormatComplianceTraversal(), store, decision_id, max_depth=2)

    history = [node for node in context.nodes if node.type == "historical_compliance"][0]
    assert history.properties["provenance"] == "enriched"
    assert history.properties["pass_rate"] == 0.96


def test_fixture_fallback_works_without_enrichment() -> None:
    store, decision_id = _price_store()

    context = _context(PriceVarianceTraversal(), store, decision_id, max_depth=1)

    commodity = [node for node in context.nodes if node.type == "commodity_index"][0]
    assert commodity.properties["provenance"] == "fixture"


def test_situation_overall_provenance_changes_to_context_after_enrichment() -> None:
    store, decision_id = _price_store()
    client = _client(store)

    before = client.get(f"/api/s2p/situation/{decision_id}?max_depth=1").json()
    S2PSituationEnricher(store).enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
    )
    after = client.get(f"/api/s2p/situation/{decision_id}?max_depth=1").json()

    assert before["provenance"]["overall"] == "sample"
    assert after["provenance"]["overall"] == "context"


def test_enrich_context_endpoint_returns_200() -> None:
    store, _decision_id = _price_store()

    response = _client(store).post(
        "/api/s2p/enrich-context/S2P-INV-0001",
        json={"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
    )

    assert response.status_code == 200
    assert response.json() == {
        "nodes_written": 1,
        "invoice_id": "S2P-INV-0001",
        "linked": True,
        "warning": None,
    }


def test_enrich_context_endpoint_warns_when_enrichment_is_orphaned() -> None:
    store = InMemoryGraphStore(domain="s2p")

    response = _client(store).post(
        "/api/s2p/enrich-context/S2P-INV-NO-LINK",
        json={"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
    )

    assert response.status_code == 200
    assert response.json()["linked"] is False
    assert response.json()["warning"] == "No decision linked - enrichment may be orphaned"


def test_enrich_context_endpoint_invalid_body_returns_400() -> None:
    store, _decision_id = _price_store()

    response = _client(store).post(
        "/api/s2p/enrich-context/S2P-INV-0001",
        json={"node_type": "unsupported", "properties": {}},
    )

    assert response.status_code == 400


def test_enrich_context_endpoint_missing_node_type_returns_422() -> None:
    store, _decision_id = _price_store()

    response = _client(store).post(
        "/api/s2p/enrich-context/S2P-INV-0001",
        json={"properties": {"commodity": "Copper"}},
    )

    assert response.status_code == 422


def test_enrich_context_endpoint_missing_properties_returns_422() -> None:
    store, _decision_id = _price_store()

    response = _client(store).post(
        "/api/s2p/enrich-context/S2P-INV-0001",
        json={"node_type": "commodity_index"},
    )

    assert response.status_code == 422


def test_enrichment_does_not_modify_decisions() -> None:
    store, _decision_id = _price_store()
    before = store.count_decisions("s2p")

    response = _client(store).post(
        "/api/s2p/enrich-context/S2P-INV-0001",
        json={"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
    )

    assert response.status_code == 200
    assert store.count_decisions("s2p") == before


def test_situation_enricher_uses_reader_for_decision_links_and_reads():
    store, decision_id = _price_store()
    reader = S2PGraphReader(store=store)
    enricher = S2PSituationEnricher(store, reader=reader)

    enricher.enrich_invoice_context(
        "S2P-INV-0001",
        {"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
    )

    assert enricher.reader is reader
    assert any(link["decision_id"] == decision_id for link in store.get_decision_links(domain="s2p"))


def test_situation_enricher_has_no_typeerror_signature_retry():
    source = inspect.getsource(situation_graph_enrichment)

    assert "except TypeError" not in source


def test_situation_enricher_propagates_graph_unavailable_error():
    store, _decision_id = _price_store()

    class FailingReader(S2PGraphReader):
        def get_decision_links(self, decision_id=None, limit=None):
            raise GraphUnavailableError("graph unavailable")

    enricher = S2PSituationEnricher(store, reader=FailingReader(store=store))

    with pytest.raises(GraphUnavailableError):
        enricher.enrich_invoice_context(
            "S2P-INV-0001",
            {"node_type": "commodity_index", "properties": {"commodity": "Copper"}},
        )


def test_reader_filters_cross_domain_decision_links():
    class CrossDomainStore:
        def __init__(self):
            self.decisions = [
                {"decision_id": "D-1", "domain": "soc"},
                {"decision_id": "D-1", "domain": "s2p"},
            ]
            self.links = [
                {"decision_id": "D-1", "domain": "soc", "entity_id": "INV-1"},
                {"decision_id": "D-1", "domain": "s2p", "entity_id": "INV-1"},
            ]

        def get_decision(self, decision_id, domain=None):
            return next(
                (dict(row) for row in self.decisions if row["decision_id"] == decision_id and row["domain"] == domain),
                None,
            )

        def get_decision_links(self, decision_id=None, domain=None, limit=None):
            rows = [
                dict(row)
                for row in self.links
                if row["domain"] == domain
                and (decision_id is None or row["decision_id"] == decision_id)
            ]
            return rows if limit is None else rows[:limit]

    reader = S2PGraphReader(CrossDomainStore())

    assert reader.get_decision("D-1")["domain"] == "s2p"
    assert [row["domain"] for row in reader.get_decision_links("D-1")] == ["s2p"]
