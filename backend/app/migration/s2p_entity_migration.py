"""Hardened, non-destructive S2P entity seed/migration writer.

This module deliberately has no production-graph default. Callers must pass an
explicit graph name and the writer never exposes the legacy force-delete path.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Mapping, cast

from app.seed_graph import (
    _load_invoices,
    _load_suppliers,
    _slug,
    seed_s2p_graph,
)

S2P_DOMAIN = "s2p"
SEED_PROVENANCE = "seed"
MIGRATION_SOURCE = "migration"

REQUIRED_ENTITY_KEYS = {
    "Invoice": "invoice_id",
    "Supplier": "supplier_id",
    "PurchaseOrder": "po_id",
    "GoodsReceipt": "gr_id",
    "Commodity": "commodity_id",
    "Contract": "contract_id",
    "Decision": "decision_id",
}

REQUIRED_EDGE_TYPES = (
    "MATCHED_TO",
    "RECEIVED_AS",
    "SUPPLIED_BY",
    "HAS_COMMODITY_INDEX",
    "GOVERNED_BY",
    "DECIDED_ON",
)


@dataclass(frozen=True)
class MigrationResult:
    """Counts returned by one idempotent migration run."""

    created_nodes: int
    updated_nodes: int
    created_edges: int
    updated_edges: int
    reconciled_orphans: int
    retained_orphans: int
    index_created: bool


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def _line_quantity(invoice: Mapping[str, Any]) -> float:
    metadata = invoice.get("metadata")
    if isinstance(metadata, Mapping):
        line_items = metadata.get("line_items")
        if isinstance(line_items, list):
            total = sum(
                _as_float(item.get("quantity"))
                for item in line_items
                if isinstance(item, Mapping)
            )
            if total > 0:
                return total
    return 1.0


def _payment_days(invoice: Mapping[str, Any]) -> float:
    metadata = invoice.get("metadata")
    if isinstance(metadata, Mapping):
        invoice_date = metadata.get("invoice_date")
        due_date = metadata.get("due_date")
        if invoice_date and due_date:
            try:
                start = dt.date.fromisoformat(str(invoice_date))
                end = dt.date.fromisoformat(str(due_date))
                return float((end - start).days)
            except ValueError:
                pass
    return 30.0


def _s3_contract_override(invoice_id: str) -> dict[str, Any]:
    if invoice_id != "S2P-INV-0003":
        return {}
    return {
        "amount": 3781.7,
        "quantity": 100.0,
        "payment_days": 30.0,
        "supplier_exception_rate": 0.033,
        "commodity_volatility": 0.35,
        "contract_max_amount": 5000.0,
        "contract_tax_compliant": True,
        "contract_regulatory_status": "approved",
    }


def _invoice_properties(invoice: Mapping[str, Any]) -> dict[str, Any]:
    invoice_id = str(invoice.get("invoice_id") or "")
    override = _s3_contract_override(invoice_id)
    amount = _as_float(invoice.get("amount"))
    quantity = _as_float(override.get("quantity"), _line_quantity(invoice))
    return {
        "amount": _as_float(override.get("amount"), amount),
        "quantity": quantity,
        "payment_days": _as_float(
            override.get("payment_days"), _payment_days(invoice)
        ),
    }


def build_hardened_seed_plan(
    *,
    seed: int = 42,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return the base seed plan normalized to the Track-1 contract."""

    nodes, edges = seed_s2p_graph(seed=seed)
    invoices = _load_invoices()
    suppliers = _load_suppliers()
    if limit is not None:
        invoices = invoices[:limit]
    invoice_by_id = {
        str(row.get("invoice_id")): row for row in invoices if row.get("invoice_id")
    }
    supplier_by_id = {
        str(row.get("supplier_id")): row
        for row in suppliers
        if row.get("supplier_id")
    }

    node_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for node in nodes:
        label = str(node["label"])
        properties = dict(node.get("properties") or {})
        key = REQUIRED_ENTITY_KEYS.get(label, str(node.get("key") or "entity_id"))
        natural_key = properties.get(key) or node.get("id")
        properties["entity_id"] = str(natural_key)
        properties.update(
            domain=S2P_DOMAIN,
            provenance=SEED_PROVENANCE,
            domain_source=MIGRATION_SOURCE,
        )
        node["properties"] = properties
        node_by_identity[(label, str(natural_key))] = node

    def ensure_node(
        label: str,
        natural_key: str,
        properties: dict[str, Any],
    ) -> None:
        identity = (label, str(natural_key))
        node = node_by_identity.get(identity)
        if node is None:
            node = {
                "id": f"{label}:{_slug(natural_key)}",
                "label": label,
                "properties": {},
            }
            nodes.append(node)
            node_by_identity[identity] = node
        node["properties"].update(properties)
        node["properties"].update(
            entity_id=str(natural_key),
            domain=S2P_DOMAIN,
            provenance=SEED_PROVENANCE,
            domain_source=MIGRATION_SOURCE,
        )

    for invoice_id, invoice in invoice_by_id.items():
        metadata = invoice.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        supplier_id = str(invoice.get("supplier_id") or "")
        po_id = str(invoice.get("po_number") or f"PO-{invoice_id}")
        gr_id = f"GR-{po_id.removeprefix('PO-')}"
        commodity_name = str(metadata.get("commodity") or "demo")
        commodity_id = _slug(commodity_name)
        contract_id = str(metadata.get("contract_ref") or f"CTR-{invoice_id}")
        invoice_props = _invoice_properties(invoice)
        override = _s3_contract_override(invoice_id)
        supplier = supplier_by_id.get(supplier_id, {})
        supplier_exception = _as_float(supplier.get("exception_rate"), 0.0)
        supplier_terms = str(supplier.get("payment_terms") or "Net 30")
        amount = invoice_props["amount"]
        quantity = invoice_props["quantity"]
        category = str(invoice.get("category") or "")
        compliant = category != "contract_gap"
        ensure_node(
            "Invoice",
            invoice_id,
            {
                "invoice_id": invoice_id,
                "supplier_id": supplier_id,
                "po_number": po_id,
                **invoice_props,
            },
        )
        ensure_node(
            "PurchaseOrder",
            po_id,
            {
                "po_id": po_id,
                "po_number": po_id,
                "supplier_id": supplier_id,
                "amount": amount,
                "quantity": quantity,
            },
        )
        ensure_node(
            "GoodsReceipt",
            gr_id,
            {
                "gr_id": gr_id,
                "po_id": po_id,
                "invoice_id": invoice_id,
                "qty_received": quantity,
                "amount": amount,
            },
        )
        ensure_node(
            "Supplier",
            supplier_id,
            {
                "supplier_id": supplier_id,
                "exception_rate": _as_float(
                    override.get("supplier_exception_rate"), supplier_exception
                ),
                "payment_terms": supplier_terms,
            },
        )
        ensure_node(
            "Commodity",
            commodity_id,
            {
                "commodity_id": commodity_id,
                "name": commodity_name,
                "volatility": _as_float(
                    override.get("commodity_volatility"),
                    _as_float(invoice.get("factors", {}).get(
                        "commodity_index_correlation"
                    ), 0.35),
                ),
            },
        )
        ensure_node(
            "Contract",
            contract_id,
            {
                "contract_id": contract_id,
                "supplier_id": supplier_id,
                "commodity_id": commodity_id,
                "max_amount": _as_float(
                    override.get("contract_max_amount"),
                    amount * (0.9 if not compliant else 1.25),
                ),
                "tax_compliant": bool(
                    override.get("contract_tax_compliant", compliant)
                ),
                "regulatory_status": str(
                    override.get(
                        "contract_regulatory_status",
                        "suspended" if not compliant else "approved",
                    )
                ),
            },
        )

    # S3-INV-0003 is the canonical integration anchor. Its values must win if
    # the fixture reuses SUP-003, resin, or CTR-003-PRI for later invoices.
    if "S2P-INV-0003" in invoice_by_id:
        ensure_node(
            "Supplier",
            "SUP-003",
            {
                "supplier_id": "SUP-003",
                "exception_rate": 0.033,
                "payment_terms": "Net 30",
            },
        )
        ensure_node(
            "Commodity",
            "resin",
            {"commodity_id": "resin", "volatility": 0.35},
        )
        ensure_node(
            "Contract",
            "CTR-003-PRI",
            {
                "contract_id": "CTR-003-PRI",
                "max_amount": 5000.0,
                "tax_compliant": True,
                "regulatory_status": "approved",
            },
        )

    edge_keys: set[tuple[str, str, str, str, str]] = set()
    normalized_edges: list[dict[str, Any]] = []
    node_by_id = {str(node.get("id")): node for node in nodes}

    def add_edge(
        edge_type: str,
        from_label: str,
        from_key: str,
        from_value: str,
        to_label: str,
        to_key: str,
        to_value: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        token = (
            edge_type,
            from_label,
            str(from_value),
            to_label,
            str(to_value),
        )
        if token in edge_keys:
            return
        edge_keys.add(token)
        normalized_edges.append(
            {
                "type": edge_type,
                "from": {"label": from_label, "key": from_key, "value": from_value},
                "to": {"label": to_label, "key": to_key, "value": to_value},
                "properties": {
                    "domain": S2P_DOMAIN,
                    "provenance": SEED_PROVENANCE,
                    "domain_source": MIGRATION_SOURCE,
                    **(properties or {}),
                },
            }
        )

    # Preserve non-entity/process edges from the base seed, but replace its
    # legacy invoice relationship names with the canonical Track-1 edges.
    for edge in edges:
        edge_type = str(edge.get("type") or edge.get("label") or "")
        if edge_type in {"INVOICED_BY", "REFERENCES", "MATCHED_TO"}:
            continue
        source_node = node_by_id.get(str(edge.get("from_id")))
        target_node = node_by_id.get(str(edge.get("to_id")))
        if source_node is None or target_node is None:
            continue
        source_properties = source_node["properties"]
        target_properties = target_node["properties"]
        source_label = str(source_node["label"])
        target_label = str(target_node["label"])
        source_key = REQUIRED_ENTITY_KEYS.get(source_label, "entity_id")
        target_key = REQUIRED_ENTITY_KEYS.get(target_label, "entity_id")
        add_edge(
            edge_type,
            source_label,
            source_key,
            str(source_properties.get(source_key, source_properties["entity_id"])),
            target_label,
            target_key,
            str(target_properties.get(target_key, target_properties["entity_id"])),
            dict(edge.get("properties") or {}),
        )

    for invoice_id, invoice in invoice_by_id.items():
        metadata = invoice.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        supplier_id = str(invoice.get("supplier_id") or "")
        po_id = str(invoice.get("po_number") or f"PO-{invoice_id}")
        gr_id = f"GR-{po_id.removeprefix('PO-')}"
        commodity_id = _slug(str(metadata.get("commodity") or "demo"))
        contract_id = str(metadata.get("contract_ref") or f"CTR-{invoice_id}")
        add_edge(
            "MATCHED_TO", "Invoice", "invoice_id", invoice_id,
            "PurchaseOrder", "po_id", po_id,
        )
        add_edge(
            "RECEIVED_AS", "Invoice", "invoice_id", invoice_id,
            "GoodsReceipt", "gr_id", gr_id,
        )
        add_edge(
            "SUPPLIED_BY", "Invoice", "invoice_id", invoice_id,
            "Supplier", "supplier_id", supplier_id,
        )
        add_edge(
            "HAS_COMMODITY_INDEX", "Invoice", "invoice_id", invoice_id,
            "Commodity", "commodity_id", commodity_id,
        )
        add_edge(
            "GOVERNED_BY", "Invoice", "invoice_id", invoice_id,
            "Contract", "contract_id", contract_id,
        )
        decision_id = f"decision_{invoice_id}"
        add_edge(
            "DECIDED_ON", "Decision", "decision_id", decision_id,
            "Invoice", "invoice_id", invoice_id,
        )

    return {"nodes": nodes, "edges": normalized_edges}


def _serialize(client: Any, value: Any) -> str:
    return cast(str, client._S(value))


def _props_literal(client: Any, properties: Mapping[str, Any]) -> str:
    parts = [
        f"{key}: {_serialize(client, value)}"
        for key, value in properties.items()
    ]
    return "{" + ", ".join(parts) + "}"


async def _upsert_node(client: Any, node: Mapping[str, Any]) -> tuple[bool, bool]:
    label = str(node["label"])
    properties = dict(node.get("properties") or {})
    entity_id = str(properties["entity_id"])
    identity = _serialize(client, entity_id)
    natural_key = REQUIRED_ENTITY_KEYS.get(label, "entity_id")
    existing = await client.run_query(
        f"MATCH (n:{label}) WHERE n.entity_id = {identity} "
        f"OR n.{natural_key} = {identity} RETURN n LIMIT 1",
        None,
    )
    if existing:
        await client.run_query(
            f"MATCH (n:{label}) WHERE n.entity_id = {identity} "
            f"OR n.{natural_key} = {identity} "
            f"SET n += {_props_literal(client, properties)} RETURN n",
            None,
        )
        return False, True
    await client.run_query(
        f"CREATE (n:{label} {_props_literal(client, properties)}) RETURN n",
        None,
    )
    return True, False


async def _upsert_edge(client: Any, edge: Mapping[str, Any]) -> tuple[bool, bool]:
    source = edge["from"]
    target = edge["to"]
    edge_type = str(edge["type"])
    source_value = _serialize(client, source["value"])
    target_value = _serialize(client, target["value"])
    match = (
        f"MATCH (a:{source['label']} {{"
        f"{source['key']}: {source_value}}})"
        f"-[r:{edge_type}]->"
        f"(b:{target['label']} {{"
        f"{target['key']}: {target_value}}})"
    )
    existing = await client.run_query(f"{match} RETURN r LIMIT 1", None)
    properties = dict(edge.get("properties") or {})
    properties.setdefault("domain", S2P_DOMAIN)
    properties.setdefault("provenance", MIGRATION_SOURCE)
    properties.setdefault("domain_source", MIGRATION_SOURCE)
    if existing:
        await client.run_query(
            f"{match} SET r += {_props_literal(client, properties)} RETURN r",
            None,
        )
        return False, True
    await client.run_query(
        f"""
        MATCH (a:{source['label']} {{{source['key']}: {source_value}}})
        MATCH (b:{target['label']} {{{target['key']}: {target_value}}})
        CREATE (a)-[r:{edge_type} {_props_literal(client, properties)}]->(b)
        RETURN r
        """,
        None,
    )
    return True, False


async def _ensure_invoice_index(dsn: str, graph_name: str) -> bool:
    if not re.fullmatch(r"protocol_v2_test_s2p_[A-Za-z0-9_]+", graph_name):
        raise ValueError("unsafe disposable graph name")
    import psycopg

    query = f'''CREATE INDEX ON "{graph_name}"."Invoice"
    USING btree (agtype_access_operator(
      VARIADIC ARRAY[properties, '"invoice_id"'::agtype]
    ))'''
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:  # type: ignore[var-annotated]
            conn.execute("LOAD 'age'")
            conn.execute("SET search_path = ag_catalog, '$user', public")
            conn.execute(query)
        return True
    except Exception as exc:
        if "already exists" in str(exc).lower():
            return False
        raise


async def _stamp_domain_edges(client: Any) -> None:
    """Normalize provenance on every S2P edge retained by the migration graph."""
    await client.run_query(
        """
        MATCH ()-[r]->()
        WHERE r.domain = 's2p'
        SET r += {domain: 's2p', provenance: 'seed', domain_source: 'migration'}
        RETURN count(r)
        """,
        None,
    )


async def _reconcile_orphans(client: Any) -> tuple[int, int]:
    rows = await client.run_query(
        """
        MATCH (d:DecisionEntityLink)
        WHERE d.domain = 's2p'
        RETURN d.decision_id AS decision_id, d.entity_id AS entity_id
        """,
        None,
    )
    reconciled = 0
    retained = 0
    for row in rows:
        decision_id = row.get("decision_id")
        entity_id = row.get("entity_id")
        if not decision_id or not entity_id:
            retained += 1
            continue
        result = await client.run_query(
            f"""
            MATCH (d:Decision {{decision_id: {_serialize(client, decision_id)}}})
            WHERE d.domain = 's2p'
            MATCH (i:Invoice {{invoice_id: {_serialize(client, entity_id)}}})
            WHERE i.domain = 's2p'
            RETURN d, i
            """,
            None,
        )
        if result:
            edge_rows = await client.run_query(
                f"""
                MATCH (d:Decision {{decision_id: {_serialize(client, decision_id)}}})
                WHERE d.domain = 's2p'
                MATCH (i:Invoice {{invoice_id: {_serialize(client, entity_id)}}})
                WHERE i.domain = 's2p'
                MATCH (d)-[r:DECIDED_ON]->(i)
                RETURN r LIMIT 1
                """,
                None,
            )
            if edge_rows:
                await client.run_query(
                    f"""
                    MATCH (d:Decision {{decision_id: {_serialize(client, decision_id)}}})
                    WHERE d.domain = 's2p'
                    MATCH (i:Invoice {{invoice_id: {_serialize(client, entity_id)}}})
                    WHERE i.domain = 's2p'
                    MATCH (d)-[r:DECIDED_ON]->(i)
                    SET r += {{domain: 's2p', provenance: 'seed',
                              domain_source: 'migration',
                              decision_id: {_serialize(client, decision_id)},
                              entity_id: {_serialize(client, entity_id)},
                              edge_type: 'DECIDED_ON'}}
                    RETURN r
                    """,
                    None,
                )
            else:
                await client.run_query(
                    f"""
                    MATCH (d:Decision {{decision_id: {_serialize(client, decision_id)}}})
                    WHERE d.domain = 's2p'
                    MATCH (i:Invoice {{invoice_id: {_serialize(client, entity_id)}}})
                    WHERE i.domain = 's2p'
                    CREATE (d)-[r:DECIDED_ON {{domain: 's2p', provenance: 'seed',
                              domain_source: 'migration',
                              decision_id: {_serialize(client, decision_id)},
                              entity_id: {_serialize(client, entity_id)},
                              edge_type: 'DECIDED_ON'}}]->(i)
                    RETURN r
                    """,
                    None,
                )
            await client.run_query(
                f"""
                MATCH (d:DecisionEntityLink {{decision_id: {_serialize(client, decision_id)}}})
                WHERE d.entity_id = {_serialize(client, entity_id)}
                  AND d.domain = 's2p'
                DELETE d
                RETURN d
                """,
                None,
            )
            reconciled += 1
        else:
            retained += 1
    return reconciled, retained


async def write_s2p_entity_migration(
    *,
    dsn: str,
    graph_name: str,
    seed: int = 42,
    limit: int | None = None,
) -> MigrationResult:
    """Write the hardened plan to one explicitly selected AGE graph.

    There is intentionally no force parameter and no DELETE path for seeded
    nodes. Existing nodes are updated only with the stamped contract fields.
    """

    graph_name = str(graph_name).strip()
    if not graph_name or graph_name == "soc_graph":
        raise ValueError("migration requires an explicit disposable non-soc_graph")
    if not graph_name.startswith("protocol_v2_test_s2p_"):
        raise ValueError("migration target must be protocol_v2_test_s2p_*")

    from ci_platform.graph.age_client import AGEClient

    plan = build_hardened_seed_plan(seed=seed, limit=limit)
    client = AGEClient(dsn=dsn, graph_name=graph_name, use_pool=True)
    await client.ensure_graph()
    created_nodes = updated_nodes = created_edges = updated_edges = 0
    try:
        for node in plan["nodes"]:
            created, updated = await _upsert_node(client, node)
            created_nodes += int(created)
            updated_nodes += int(updated)
        for edge in plan["edges"]:
            created, updated = await _upsert_edge(client, edge)
            created_edges += int(created)
            updated_edges += int(updated)
        await _stamp_domain_edges(client)
        index_created = await _ensure_invoice_index(dsn, graph_name)
        reconciled, retained = await _reconcile_orphans(client)
    finally:
        await client.close()
    return MigrationResult(
        created_nodes=created_nodes,
        updated_nodes=updated_nodes,
        created_edges=created_edges,
        updated_edges=updated_edges,
        reconciled_orphans=reconciled,
        retained_orphans=retained,
        index_created=index_created,
    )


def write_s2p_entity_migration_sync(**kwargs: Any) -> MigrationResult:
    """Synchronous wrapper for CLI/admin callers."""

    return asyncio.run(write_s2p_entity_migration(**kwargs))
