"""Commit 2 Track-2 reader and real-factor integration tests."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.domains.s2p.factors import (  # noqa: E402
    MatchStatus,
    TaxRegulatoryCompliance,
    compute_all_factors,
)
from app.graph.s2p_graph_reader import S2PGraphReader  # noqa: E402
from app.main import app, build_s2p_scorer  # noqa: E402
from app.migration.s2p_entity_migration import (  # noqa: E402
    write_s2p_entity_migration,
)
from app.routers.s2p import _resolve_graph_context  # noqa: E402
from app.s2p_graph_status import (  # noqa: E402
    S2PActiveGraphConfig,
    create_s2p_active_graph_store,
)


TARGET = {
    "invoice_id": "S2P-INV-0003",
    "amount": 3781.7,
    "quantity": 100,
    "supplier_id": "SUP-003",
}


@pytest.fixture(scope="module")
def seeded_track2(s2p_age_test_env):
    """Seed the shared test-only AGE graph using Commit 1's real writer."""
    dsn = s2p_age_test_env.active["S2P_ACTIVE_AGE_DSN"]
    graph = s2p_age_test_env.active["S2P_ACTIVE_AGE_GRAPH"]
    result = asyncio.run(write_s2p_entity_migration(dsn=dsn, graph_name=graph))
    config = S2PActiveGraphConfig.from_env(s2p_age_test_env.active)
    store = create_s2p_active_graph_store(config)
    assert store is not None
    return S2PGraphReader(store), store


def _request_for_store(store: Any) -> Request:
    app.state.scorer = build_s2p_scorer(graph_store=store)
    app.state.graph_store = store
    return Request({"type": "http", "method": "GET", "path": "/", "app": app})


def _concrete_store(reader: S2PGraphReader) -> Any:
    return reader._age_store()


def test_query_direct_context_returns_entities(seeded_track2):
    reader, _store = seeded_track2
    rows = reader.query_direct_context(TARGET["invoice_id"])
    nodes = [row["node"] for row in rows]
    keys = {key for node in nodes for key in node}
    assert {"po_id", "gr_id", "supplier_id", "commodity_id", "contract_id"} <= keys
    assert all(row.get("node", {}).get("domain") == "s2p" for row in rows)
    assert not any("decision_id" in node for node in nodes)


def test_query_direct_context_excludes_decisions(seeded_track2):
    reader, _store = seeded_track2
    rows = reader.query_direct_context(TARGET["invoice_id"], limit=100)
    assert len(rows) >= 5
    identifiers = [
        (row["node"].get("invoice_id"), row["node"].get("entity_id"), sorted(row["node"]))
        for row in rows
    ]
    entity_ids = {entity_id for _invoice_id, entity_id, _keys in identifiers}
    assert {"PO-20260003", "GR-20260003", "SUP-003", "CTR-003-PRI"} <= entity_ids
    assert any("commodity_id" in keys for _invoice_id, _entity_id, keys in identifiers)
    assert all("decision_id" not in row["node"] for row in rows)


def test_query_duplicate_context_bounded(seeded_track2):
    reader, _store = seeded_track2
    rows = reader.query_duplicate_context(
        TARGET["invoice_id"], TARGET["supplier_id"], TARGET["amount"], limit=1
    )
    assert len(rows) <= 1
    assert all(row["node"].get("invoice_id") != TARGET["invoice_id"] for row in rows)


def test_real_match_status_perturbation(seeded_track2):
    reader, _store = seeded_track2
    factor = MatchStatus()
    invoice = {**TARGET}
    context = {"neighbors": reader.query_direct_context(TARGET["invoice_id"])}
    before = factor.compute(invoice, context)
    assert before == pytest.approx(1.0), [
        (node.get("_label"), node.get("entity_id"), node.get("contract_id"),
         node.get("max_amount"), node.get("tax_compliant"), node.get("regulatory_status"))
        for node in (row["node"] for row in context["neighbors"])
    ]
    assert factor.last_provenance == "computed"

    store = _concrete_store(reader)
    changed = store._run_query(
        "MATCH (po:PurchaseOrder {entity_id: 'PO-20260003'}) "
        "SET po.amount = 2000.0 RETURN po"
    )
    try:
        changed_context = {"neighbors": reader.query_direct_context(TARGET["invoice_id"])}
        after = factor.compute(invoice, changed_context)
        assert after < before, {
            "set_result": changed,
            "purchase_orders": [
                (row["node"].get("po_id"), row["node"].get("entity_id"), row["node"].get("amount"))
                for row in changed_context["neighbors"]
                if "po_id" in row["node"]
            ],
        }
        assert factor.last_provenance == "computed"
    finally:
        store._run_query(
            "MATCH (po:PurchaseOrder {entity_id: 'PO-20260003'}) "
            "SET po.amount = 3781.7 RETURN po"
        )


def test_real_tax_reg_perturbation(seeded_track2):
    reader, _store = seeded_track2
    factor = TaxRegulatoryCompliance()
    invoice = {**TARGET}
    context = {"neighbors": reader.query_direct_context(TARGET["invoice_id"])}
    before = factor.compute(invoice, context)
    assert before == pytest.approx(1.0), [
        (
            node.get("_label"), node.get("entity_id"), node.get("contract_id"),
            node.get("max_amount"), node.get("tax_compliant"),
            node.get("regulatory_status"),
        )
        for node in (row["node"] for row in context["neighbors"])
    ]
    assert factor.last_provenance == "computed"

    store = _concrete_store(reader)
    store._run_query(
        "MATCH (c:Contract {entity_id: 'CTR-003-PRI'}) "
        "SET c.tax_compliant = false RETURN c"
    )
    try:
        changed_context = {"neighbors": reader.query_direct_context(TARGET["invoice_id"])}
        after = factor.compute(invoice, changed_context)
        assert after < before
        assert factor.last_provenance == "computed"
    finally:
        store._run_query(
            "MATCH (c:Contract {entity_id: 'CTR-003-PRI'}) "
            "SET c.tax_compliant = true RETURN c"
        )


def test_resolve_graph_context_and_all_factors_use_directed_rows(seeded_track2):
    reader, store = seeded_track2
    app.state.s2p_graph_reader = reader
    request = _request_for_store(store)
    context = _resolve_graph_context(
        TARGET["invoice_id"],
        request,
        supplier_id=TARGET["supplier_id"],
        amount=TARGET["amount"],
    )
    assert context is not None
    values = compute_all_factors({**TARGET}, context=context)
    assert values["match_status"] == pytest.approx(1.0)
    assert values["amount_variance_ratio"] == pytest.approx(0.0)
    assert values["supplier_exception_history"] == pytest.approx(0.033)
    assert values["commodity_index_correlation"] == pytest.approx(0.35)
    assert values["tax_regulatory_compliance"] == pytest.approx(1.0)


def test_score_endpoint_uses_graph_factor_vector(seeded_track2):
    reader, store = seeded_track2
    app.state.s2p_graph_reader = reader
    _request_for_store(store)
    response = TestClient(app).post(
        "/api/s2p/score",
        json={
            "event_id": TARGET["invoice_id"],
            "category": "price_variance",
            "amount": TARGET["amount"],
            "supplier_id": TARGET["supplier_id"],
            "contract_id": "CTR-003-PRI",
        },
    )
    assert response.status_code == 200
    vector = response.json()["factor_vector"]
    assert vector[0] == pytest.approx(1.0)
    assert vector[1] == pytest.approx(0.0)
    assert vector[3] == pytest.approx(0.033)
    assert vector[5] == pytest.approx(0.35)
    assert vector[6] == pytest.approx(1.0)
