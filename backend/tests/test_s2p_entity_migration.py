"""Disposable AGE tests for the hardened S2P Track-1 writer."""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any, cast

import psycopg

from app.migration.s2p_entity_migration import (
    write_s2p_entity_migration,
)
from app.routers.s2p import score_procurement_event


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _query(dsn: str, graph: str, cypher: str) -> list[dict[str, Any]]:
    from ci_platform.graph.age_client import AGEClient

    async def execute() -> list[dict[str, Any]]:
        client = AGEClient(dsn=dsn, graph_name=graph)
        try:
            return await client.run_query(cypher, None)
        finally:
            await client.close()

    return _run(execute())


def test_seed_idempotent_contract_edges_and_index(s2p_age_test_env: Any) -> None:
    dsn = s2p_age_test_env.active["S2P_ACTIVE_AGE_DSN"]
    graph = s2p_age_test_env.active["S2P_ACTIVE_AGE_GRAPH"]

    first = _run(write_s2p_entity_migration(dsn=dsn, graph_name=graph))
    second = _run(write_s2p_entity_migration(dsn=dsn, graph_name=graph))

    assert first.created_nodes > 0
    assert first.created_edges > 0
    assert second.created_nodes == 0
    assert second.created_edges == 0
    assert second.updated_nodes > 0
    assert second.updated_edges > 0

    rows = _query(
        dsn,
        graph,
        """
        MATCH (n:Invoice {invoice_id: 'S2P-INV-0003'})
        RETURN properties(n) AS props
        """,
    )
    invoice = rows[0]["props"]
    assert invoice["entity_id"] == "S2P-INV-0003"
    assert invoice["amount"] == 3781.7
    assert invoice["quantity"] == 100.0
    assert invoice["payment_days"] == 30.0

    expected = {
        "PurchaseOrder": ("PO-20260003", {"amount": 3781.7, "quantity": 100.0}),
        "GoodsReceipt": ("GR-20260003", {"qty_received": 100.0, "amount": 3781.7}),
        "Supplier": ("SUP-003", {"exception_rate": 0.033, "payment_terms": "Net 30"}),
        "Commodity": ("resin", {"volatility": 0.35}),
        "Contract": (
            "CTR-003-PRI",
            {
                "max_amount": 5000.0,
                "tax_compliant": True,
                "regulatory_status": "approved",
            },
        ),
    }
    for label, (entity_id, fields) in expected.items():
        rows = _query(
            dsn,
            graph,
            f"MATCH (n:{label} {{entity_id: '{entity_id}'}}) "
            "RETURN properties(n) AS props",
        )
        props = rows[0]["props"]
        assert props["domain"] == "s2p"
        assert props["provenance"] == "seed"
        assert props["domain_source"] == "migration"
        fields_dict = cast(dict[str, Any], fields)
        for key, value in fields_dict.items():
            assert props[key] == value
        numeric_keys = {
            "PurchaseOrder": {"amount", "quantity"},
            "GoodsReceipt": {"qty_received", "amount"},
            "Commodity": {"volatility"},
            "Contract": {"max_amount"},
        }
        for key in numeric_keys.get(label, set()):
            assert isinstance(props[key], (int, float))
        if label == "Supplier":
            assert isinstance(props["exception_rate"], (int, float))
            assert isinstance(props["payment_terms"], str)
        if label == "Contract":
            assert isinstance(props["tax_compliant"], bool)
            assert isinstance(props["regulatory_status"], str)

    edge_rows = _query(
        dsn,
        graph,
        """
        MATCH (i:Invoice {invoice_id: 'S2P-INV-0003'})-[r]->(n)
        WHERE r.domain = 's2p'
        RETURN type(r) AS edge_type
        """,
    )
    edge_types = {row["edge_type"] for row in edge_rows}
    assert {
        "MATCHED_TO",
        "RECEIVED_AS",
        "SUPPLIED_BY",
        "HAS_COMMODITY_INDEX",
        "GOVERNED_BY",
    } <= edge_types
    decision_edges = _query(
        dsn,
        graph,
        """
        MATCH (d:Decision {decision_id: 'decision_S2P-INV-0003'})
              -[r:DECIDED_ON]->(i:Invoice {invoice_id: 'S2P-INV-0003'})
        RETURN properties(r) AS props
        """,
    )
    assert decision_edges
    assert decision_edges[0]["props"]["domain_source"] == "migration"

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("LOAD 'age'")
        indexes = conn.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = 'Invoice'",
            (graph,),
        ).fetchall()
    assert indexes


def test_seed_stamps_nodes_and_edges(s2p_age_test_env: Any) -> None:
    dsn = s2p_age_test_env.active["S2P_ACTIVE_AGE_DSN"]
    graph = s2p_age_test_env.active["S2P_ACTIVE_AGE_GRAPH"]
    _run(write_s2p_entity_migration(dsn=dsn, graph_name=graph, limit=5))

    nodes = _query(
        dsn,
        graph,
        """
        MATCH (n)
        WHERE n.domain = 's2p' AND n.domain_source = 'migration'
        RETURN properties(n) AS props
        """,
    )
    assert nodes
    assert all(
        props["provenance"] == "seed"
        and props["domain"] == "s2p"
        and props["domain_source"] == "migration"
        and props.get("entity_id")
        for props in (row["props"] for row in nodes)
    )

    edges = _query(
        dsn,
        graph,
        """
        MATCH ()-[r]->()
        WHERE r.domain = 's2p'
        RETURN properties(r) AS props
        """,
    )
    assert edges
    assert all(
        props["provenance"] == "seed"
        and props["domain"] == "s2p"
        and props["domain_source"] == "migration"
        for props in (row["props"] for row in edges)
    )


def test_orphan_reconciliation_retains_incomplete_links(
    s2p_age_test_env: Any,
) -> None:
    dsn = s2p_age_test_env.active["S2P_ACTIVE_AGE_DSN"]
    graph = s2p_age_test_env.active["S2P_ACTIVE_AGE_GRAPH"]
    _run(write_s2p_entity_migration(dsn=dsn, graph_name=graph, limit=3))

    _query(
        dsn,
        graph,
        """
        CREATE (a:DecisionEntityLink {
            domain: 's2p', decision_id: 'decision_S2P-INV-0003',
            entity_id: 'S2P-INV-0003'
        })
        CREATE (b:DecisionEntityLink {
            domain: 's2p', decision_id: 'missing-decision',
            entity_id: 'S2P-INV-0003'
        })
        RETURN a, b
        """,
    )
    result = _run(write_s2p_entity_migration(dsn=dsn, graph_name=graph, limit=3))
    assert result.reconciled_orphans >= 1
    assert result.retained_orphans >= 1

    reconciled = _query(
        dsn,
        graph,
        """
        MATCH (d:Decision {decision_id: 'decision_S2P-INV-0003'})
              -[r:DECIDED_ON]->(i:Invoice {invoice_id: 'S2P-INV-0003'})
        RETURN properties(r) AS props
        """,
    )
    assert reconciled
    assert reconciled[0]["props"]["domain_source"] == "migration"

    retained = _query(
        dsn,
        graph,
        """
        MATCH (l:DecisionEntityLink {decision_id: 'missing-decision'})
        RETURN l
        """,
    )
    assert retained


def test_migration_rejects_live_graph() -> None:
    dsn = os.environ.get("AGE_TEST_DSN", "host=localhost port=5433 dbname=soc_copilot user=postgres password=postgres")
    try:
        _run(write_s2p_entity_migration(dsn=dsn, graph_name="soc_graph"))
    except ValueError as exc:
        assert "non-soc_graph" in str(exc)
    else:
        raise AssertionError("live soc_graph must be rejected")


def test_link_off_response_path() -> None:
    source = inspect.getsource(score_procurement_event)
    assert "_submit_side_effect(" in source
    assert "_link_decision_to_invoice" in source
