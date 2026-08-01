"""S2P situation context enrichment through GraphStore entity enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from copilot_sdk.graph.enrichment import EnrichmentSourceSet, ProvenancedValue
from copilot_sdk.graph.protocol import GraphStore

from app.graph.s2p_graph_reader import S2PGraphReader

DOMAIN = "s2p"
NAMESPACE = "s2p_situation_context"
COMPUTATION_VERSION = "p39_s2p_situation_context_v1"

NODE_TYPES: dict[str, dict[str, Any]] = {
    "commodity_index": {
        "entity_type": "CommodityIndex",
        "edge_type": "HAS_COMMODITY_INDEX",
        "id_fields": ("commodity",),
    },
    "CommodityIndex": {
        "entity_type": "CommodityIndex",
        "edge_type": "HAS_COMMODITY_INDEX",
        "id_fields": ("commodity",),
    },
    "contract_clause": {
        "entity_type": "ContractClause",
        "edge_type": "GOVERNED_BY",
        "id_fields": ("ref", "contract_ref"),
    },
    "ContractClause": {
        "entity_type": "ContractClause",
        "edge_type": "GOVERNED_BY",
        "id_fields": ("ref", "contract_ref"),
    },
    "goods_receipt": {
        "entity_type": "GoodsReceipt",
        "edge_type": "RECEIVED_AS",
        "id_fields": ("gr_id",),
    },
    "GoodsReceipt": {
        "entity_type": "GoodsReceipt",
        "edge_type": "RECEIVED_AS",
        "id_fields": ("gr_id",),
    },
    "historical_compliance": {
        "entity_type": "ComplianceHistory",
        "edge_type": "COMPLIANCE_RECORD",
        "id_fields": ("rule_id", "supplier"),
    },
    "ComplianceHistory": {
        "entity_type": "ComplianceHistory",
        "edge_type": "COMPLIANCE_RECORD",
        "id_fields": ("rule_id", "supplier"),
    },
}


@dataclass
class S2PSituationEnricher:
    """Writes context nodes using GraphStore.write_entity_enrichment()."""

    graph_store: Any
    reader: S2PGraphReader | None = None
    linked: bool = False
    warning: str | None = None

    def __post_init__(self) -> None:
        if self.reader is None:
            self.reader = S2PGraphReader(store=self.graph_store)

    def enrich_invoice_context(self, invoice_id: str, context: dict[str, Any]) -> int:
        """Write context nodes for an invoice. Returns count of newly written nodes."""
        nodes = _context_nodes(context)
        written = 0
        linked_any = False
        for node in nodes:
            node_type = str(node.get("node_type") or node.get("type") or context.get("node_type") or "")
            config = NODE_TYPES.get(node_type)
            if config is None:
                raise ValueError(f"unsupported context node_type: {node_type}")
            properties = dict(node.get("properties") or {})
            properties["provenance"] = "enriched"
            entity_type = str(config["entity_type"])
            entity_id = _entity_id(invoice_id, entity_type, properties, config["id_fields"])
            existed = bool(self._read(entity_type, entity_id))
            self._write(entity_type, entity_id, properties, invoice_id)
            self._link_invoice_decisions(invoice_id, entity_id, str(config["edge_type"]))
            if self.reader is not None:
                linked_any = linked_any or _has_entity_link(
                    self.reader, entity_id, str(config["edge_type"])
                )
            if not existed:
                written += 1
        self.linked = linked_any
        self.warning = None if linked_any else "No decision linked - enrichment may be orphaned"
        return written

    def _read(self, entity_type: str, entity_id: str) -> dict[str, ProvenancedValue]:
        if not isinstance(self.graph_store, GraphStore):
            return {}
        result = self.graph_store.read_entity_enrichment(
            domain=DOMAIN, entity_type=entity_type, entity_id=entity_id, namespace=NAMESPACE
        )
        return result if isinstance(result, dict) else {}

    def _write(
        self,
        entity_type: str,
        entity_id: str,
        properties: dict[str, Any],
        invoice_id: str,
    ) -> None:
        if not isinstance(self.graph_store, GraphStore):
            raise ValueError("GraphStore write_entity_enrichment API unavailable")
        self.graph_store.write_entity_enrichment(
            domain=DOMAIN,
            entity_type=entity_type,
            entity_id=entity_id,
            namespace=NAMESPACE,
            metrics=_metrics(properties),
            computed_from=EnrichmentSourceSet(
                integration_sources=["GraphStore.write_entity_enrichment"],
                computation_version=COMPUTATION_VERSION,
            ),
            dry_run=False,
            idempotency_key=f"{COMPUTATION_VERSION}:{invoice_id}:{entity_type}:{entity_id}",
        )

    def _link_invoice_decisions(self, invoice_id: str, entity_id: str, edge_type: str) -> None:
        linker = getattr(self.graph_store, "link_decision_to_entity", None)
        if not callable(linker):
            return
        if self.reader is None:
            return
        existing_links = _decision_links(self.reader)
        decision_ids = sorted(
            {
                str(link.get("decision_id"))
                for link in existing_links
                if str(link.get("entity_id")) == str(invoice_id) and link.get("decision_id")
            }
        )
        if not decision_ids and _has_decision(self.reader, invoice_id):
            decision_ids = [str(invoice_id)]
        for decision_id in decision_ids:
            if any(
                str(link.get("decision_id")) == decision_id
                and str(link.get("entity_id")) == entity_id
                and str(link.get("edge_type")) == edge_type
                for link in existing_links
            ):
                continue
            linker(decision_id, entity_id, edge_type=edge_type, domain=DOMAIN)


def _context_nodes(context: dict[str, Any]) -> list[dict[str, Any]]:
    raw_nodes = context.get("nodes")
    if isinstance(raw_nodes, list):
        return [node for node in raw_nodes if isinstance(node, dict)]
    return [context]


def _entity_id(invoice_id: str, entity_type: str, properties: dict[str, Any], id_fields: tuple[str, ...]) -> str:
    for field in id_fields:
        value = properties.get(field)
        if value not in (None, ""):
            return f"{entity_type}:{value}"
    return f"{entity_type}:{invoice_id}"


def _metrics(properties: dict[str, Any]) -> dict[str, ProvenancedValue]:
    return {
        str(key): ProvenancedValue(
            value=value,
            source="enriched",
            provenance_tier="context",
            source_count=1,
            factor_eligible=False,
            provenance_label="S2P situation graph enrichment",
            measured=True,
            verified=False,
        )
        for key, value in sorted(properties.items())
        if value is not None
    }


def _decision_links(reader: S2PGraphReader) -> list[dict[str, Any]]:
    rows = reader.get_decision_links(limit=1000)
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _has_decision(reader: S2PGraphReader, decision_id: str) -> bool:
    return isinstance(reader.get_decision(str(decision_id)), dict)


def _has_entity_link(reader: S2PGraphReader, entity_id: str, edge_type: str) -> bool:
    return any(
        str(link.get("entity_id")) == str(entity_id)
        and str(link.get("edge_type")) == str(edge_type)
        for link in _decision_links(reader)
    )


__all__ = [
    "COMPUTATION_VERSION",
    "DOMAIN",
    "NAMESPACE",
    "NODE_TYPES",
    "S2PSituationEnricher",
]
