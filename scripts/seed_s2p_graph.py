"""Seed the S2P AGE graph from Phase 0 and D-CEL fixtures."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.graph_contract import S2P_GRAPH_CONTRACT


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_invoices(path: Path) -> List[Dict[str, Any]]:
    data = load_json(path, [])
    return data if isinstance(data, list) else []


def load_suppliers(path: Path) -> List[Dict[str, Any]]:
    data = load_json(path, [])
    return data if isinstance(data, list) else []


def process_data_paths() -> List[Path]:
    paths: List[Path] = []
    sdk_root = os.environ.get("CLAUDE_SDK")
    if sdk_root:
        paths.append(
            Path(sdk_root)
            / "apps"
            / "dataops"
            / "backend"
            / "data"
            / "celonis_process_data.json"
        )
    paths.append(REPO_ROOT / "data" / "celonis_process_data.json")
    return paths


def load_process_data(paths: Iterable[Path] | None = None) -> Dict[str, Any]:
    for path in paths or process_data_paths():
        data = load_json(path, {})
        if isinstance(data, dict) and data:
            return data
    return {}


def _slug(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    cleaned = [ch if ch.isalnum() else "_" for ch in text]
    return "_".join("".join(cleaned).split("_")).strip("_") or "unknown"


def _add_node(
    plan: Dict[str, List[Dict[str, Any]]],
    seen: set[tuple[str, str]],
    label: str,
    key: str,
    properties: Dict[str, Any],
) -> None:
    identifier = str(properties.get(key) or "")
    if not identifier:
        return
    token = (label, identifier)
    if token in seen:
        return
    seen.add(token)
    plan["nodes"].append({"label": label, "key": key, "properties": properties})


def _add_edge(
    plan: Dict[str, List[Dict[str, Any]]],
    seen: set[tuple[str, str, str, str, str]],
    edge_type: str,
    from_label: str,
    from_key: str,
    from_value: str,
    to_label: str,
    to_key: str,
    to_value: str,
    properties: Dict[str, Any] | None = None,
) -> None:
    if not from_value or not to_value:
        return
    token = (edge_type, from_label, str(from_value), to_label, str(to_value))
    if token in seen:
        return
    seen.add(token)
    plan["edges"].append(
        {
            "type": edge_type,
            "from": {"label": from_label, "key": from_key, "value": from_value},
            "to": {"label": to_label, "key": to_key, "value": to_value},
            "properties": properties or {},
        }
    )


def build_seed_plan(
    invoices: List[Dict[str, Any]],
    suppliers: List[Dict[str, Any]],
    process_data: Dict[str, Any] | None = None,
    limit: int | None = None,
) -> Dict[str, Any]:
    selected_invoices = invoices[:limit] if limit else invoices
    process_data = process_data or {}
    plan: Dict[str, Any] = {
        "graph_name": S2P_GRAPH_CONTRACT["graph_name"],
        "nodes": [],
        "edges": [],
        "warnings": [],
    }
    seen_nodes: set[tuple[str, str]] = set()
    seen_edges: set[tuple[str, str, str, str, str]] = set()

    for supplier in suppliers:
        _add_node(
            plan,
            seen_nodes,
            "Supplier",
            "supplier_id",
            {
                "supplier_id": supplier.get("supplier_id"),
                "name": supplier.get("name"),
                "category": supplier.get("category"),
                "exception_rate": supplier.get("exception_rate"),
                "payment_terms": supplier.get("payment_terms"),
                "otif_score": supplier.get("otif_score"),
            },
        )

    supplier_ids = {supplier.get("supplier_id") for supplier in suppliers}
    for invoice in selected_invoices:
        metadata = invoice.get("metadata") or {}
        invoice_id = invoice.get("invoice_id")
        supplier_id = invoice.get("supplier_id")
        po_id = invoice.get("po_number")
        commodity = metadata.get("commodity")
        commodity_id = _slug(commodity)
        contract_id = metadata.get("contract_ref")
        gr_id = f"GR-{po_id or invoice_id}"

        _add_node(
            plan,
            seen_nodes,
            "Invoice",
            "invoice_id",
            {
                "invoice_id": invoice_id,
                "supplier_id": supplier_id,
                "po_number": po_id,
                "amount": invoice.get("amount"),
                "currency": invoice.get("currency"),
                "category": invoice.get("category"),
                "ground_truth_action": invoice.get("ground_truth_action"),
            },
        )
        _add_node(
            plan,
            seen_nodes,
            "PurchaseOrder",
            "po_id",
            {
                "po_id": po_id,
                "po_number": po_id,
                "supplier_id": supplier_id,
                "currency": invoice.get("currency"),
            },
        )
        _add_node(
            plan,
            seen_nodes,
            "GoodsReceipt",
            "gr_id",
            {
                "gr_id": gr_id,
                "po_id": po_id,
                "invoice_id": invoice_id,
                "source": "synthetic_invoices",
            },
        )
        if commodity:
            _add_node(
                plan,
                seen_nodes,
                "Commodity",
                "commodity_id",
                {"commodity_id": commodity_id, "name": commodity},
            )
        if contract_id:
            _add_node(
                plan,
                seen_nodes,
                "Contract",
                "contract_id",
                {
                    "contract_id": contract_id,
                    "supplier_id": supplier_id,
                    "commodity_id": commodity_id,
                },
            )

        if supplier_id not in supplier_ids:
            plan["warnings"].append(f"missing supplier fixture for {supplier_id}")
        _add_edge(plan, seen_edges, "INVOICED_BY", "Invoice", "invoice_id", invoice_id, "Supplier", "supplier_id", supplier_id)
        _add_edge(plan, seen_edges, "REFERENCES", "Invoice", "invoice_id", invoice_id, "PurchaseOrder", "po_id", po_id)
        _add_edge(plan, seen_edges, "MATCHED_TO", "Invoice", "invoice_id", invoice_id, "GoodsReceipt", "gr_id", gr_id)
        if contract_id and commodity:
            _add_edge(plan, seen_edges, "COVERS", "Contract", "contract_id", contract_id, "Commodity", "commodity_id", commodity_id)
        if supplier_id and commodity:
            _add_edge(plan, seen_edges, "SUPPLIES", "Supplier", "supplier_id", supplier_id, "Commodity", "commodity_id", commodity_id)

    activities = process_data.get("activities") or []
    if process_data:
        model_id = _slug(process_data.get("process_model"))
        variant_id = _slug(process_data.get("variant"))
        _add_node(
            plan,
            seen_nodes,
            "ProcessModel",
            "model_id",
            {
                "model_id": model_id,
                "name": process_data.get("process_model"),
                "source": process_data.get("source", "celonis_cache"),
            },
        )
        _add_node(
            plan,
            seen_nodes,
            "ProcessVariant",
            "variant_id",
            {
                "variant_id": variant_id,
                "name": process_data.get("variant"),
                "variant_frequency": process_data.get("variant_frequency"),
                "total_cases": process_data.get("total_cases"),
            },
        )
        _add_edge(plan, seen_edges, "HAS_VARIANT", "ProcessModel", "model_id", model_id, "ProcessVariant", "variant_id", variant_id)
        for activity in activities:
            activity_id = activity.get("id") or _slug(activity.get("name"))
            system_id = activity.get("system")
            _add_node(
                plan,
                seen_nodes,
                "Activity",
                "activity_id",
                {
                    "activity_id": activity_id,
                    "name": activity.get("name"),
                    "avg_duration_hours": activity.get("avg_duration_hours"),
                    "case_count": activity.get("case_count"),
                    "status": activity.get("status"),
                    "bottleneck": bool(activity.get("bottleneck")),
                    "bottleneck_cause": activity.get("bottleneck_cause"),
                    "system": system_id,
                },
            )
            _add_edge(plan, seen_edges, "HAS_ACTIVITY", "ProcessVariant", "variant_id", variant_id, "Activity", "activity_id", activity_id)
            if activity.get("bottleneck") and system_id:
                _add_node(
                    plan,
                    seen_nodes,
                    "PipelineSystem",
                    "system_id",
                    {
                        "system_id": system_id,
                        "name": system_id,
                        "source": "celonis_cache",
                    },
                )
                _add_edge(plan, seen_edges, "BOTTLENECK_AT", "Activity", "activity_id", activity_id, "PipelineSystem", "system_id", system_id)

        bottleneck = next((activity for activity in activities if activity.get("bottleneck")), None)
        if bottleneck:
            activity_id = bottleneck.get("id") or _slug(bottleneck.get("name"))
            insight_suppliers = {
                insight.get("supplier")
                for insight in process_data.get("cross_graph_insights", [])
                if insight.get("supplier")
            }
            supplier_name_to_id = {supplier.get("name"): supplier.get("supplier_id") for supplier in suppliers}
            target_supplier_ids = [
                supplier_name_to_id[name]
                for name in insight_suppliers
                if name in supplier_name_to_id
            ]
            if not target_supplier_ids and suppliers:
                target_supplier_ids = [suppliers[0].get("supplier_id")]
            for supplier_id in target_supplier_ids:
                _add_edge(plan, seen_edges, "INVOICE_PATTERN", "Supplier", "supplier_id", supplier_id, "Activity", "activity_id", activity_id)
    else:
        plan["warnings"].append("celonis_process_data.json not found; process nodes skipped")

    plan["summary"] = {
        "node_count": len(plan["nodes"]),
        "edge_count": len(plan["edges"]),
        "invoice_count": len(selected_invoices),
        "supplier_count": len(suppliers),
        "activity_count": len(activities),
    }
    return plan


def _serialize(client: Any, value: Any) -> str:
    return client._S(value)


def _props_literal(client: Any, properties: Dict[str, Any]) -> str:
    parts = [f"{key}: {_serialize(client, value)}" for key, value in properties.items()]
    return "{" + ", ".join(parts) + "}"


async def write_seed_plan(plan: Dict[str, Any], dsn: str, graph_name: str, force: bool = False) -> None:
    from ci_platform.graph.age_client import AGEClient

    client = AGEClient(dsn=dsn, graph_name=graph_name)
    await client.ensure_graph()
    if force:
        await client.run_query("MATCH (n) DETACH DELETE n", None)

    for node in plan["nodes"]:
        label = node["label"]
        key = node["key"]
        value = node["properties"].get(key)
        existing = await client.run_query(
            f"MATCH (n:{label} {{{key}: {_serialize(client, value)}}}) RETURN n LIMIT 1",
            None,
        )
        if not existing:
            await client.run_query(
                f"CREATE (n:{label} {_props_literal(client, node['properties'])}) RETURN n",
                None,
            )

    for edge in plan["edges"]:
        source = edge["from"]
        target = edge["to"]
        edge_type = edge["type"]
        existing = await client.run_query(
            f"""
            MATCH (a:{source['label']} {{{source['key']}: {_serialize(client, source['value'])}}})-[r:{edge_type}]->(b:{target['label']} {{{target['key']}: {_serialize(client, target['value'])}}})
            RETURN r
            LIMIT 1
            """,
            None,
        )
        if not existing:
            props = _props_literal(client, edge.get("properties", {}))
            await client.run_query(
                f"""
                MATCH (a:{source['label']} {{{source['key']}: {_serialize(client, source['value'])}}})
                MATCH (b:{target['label']} {{{target['key']}: {_serialize(client, target['value'])}}})
                CREATE (a)-[r:{edge_type} {props}]->(b)
                RETURN r
                """,
                None,
            )
    await client.close()


def default_fixture_paths() -> tuple[Path, Path]:
    return (
        REPO_ROOT / "data" / "synthetic_invoices.json",
        REPO_ROOT / "data" / "s2p_demo_suppliers.json",
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed S2P AGE graph fixtures.")
    parser.add_argument("--dsn", default=os.environ.get("GRAPH_DSN"))
    parser.add_argument("--graph-name", default=S2P_GRAPH_CONTRACT["graph_name"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    invoices_path, suppliers_path = default_fixture_paths()
    plan = build_seed_plan(
        load_invoices(invoices_path),
        load_suppliers(suppliers_path),
        load_process_data(),
        limit=args.limit,
    )

    print(
        f"{plan['summary']['node_count']} nodes, "
        f"{plan['summary']['edge_count']} edges, "
        f"graph={args.graph_name}"
    )
    for warning in plan["warnings"]:
        print(f"warning: {warning}")

    if args.dry_run:
        return 0
    if not args.dsn:
        print("error: --dsn or GRAPH_DSN is required unless --dry-run is used", file=sys.stderr)
        return 2
    asyncio.run(write_seed_plan(plan, args.dsn, args.graph_name, force=args.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
