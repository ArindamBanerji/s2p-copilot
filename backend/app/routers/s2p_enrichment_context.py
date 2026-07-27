"""S2P situation context enrichment endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.graph.s2p_graph_reader import S2PGraphReader
from app.services.situation_graph_enrichment import S2PSituationEnricher

router = APIRouter(prefix="/api/s2p", tags=["s2p-situation-enrichment"])


class EnrichContextRequest(BaseModel):
    node_type: str
    properties: dict[str, Any]


class EnrichContextResponse(BaseModel):
    nodes_written: int
    invoice_id: str
    linked: bool
    warning: str | None = None


@router.post("/enrich-context/{invoice_id}", response_model=EnrichContextResponse)
def enrich_context(invoice_id: str, payload: EnrichContextRequest, request: Request) -> EnrichContextResponse:
    graph_store = _graph_store(request)
    if graph_store is None:
        raise HTTPException(status_code=503, detail="GraphStore unavailable for S2P context enrichment")
    if not payload.node_type.strip() or not isinstance(payload.properties, dict):
        raise HTTPException(status_code=400, detail="node_type and properties are required")
    state = getattr(request.app, "state", None)
    reader = getattr(state, "s2p_graph_reader", None)
    if not isinstance(reader, S2PGraphReader):
        reader = S2PGraphReader(store=graph_store)
    service = S2PSituationEnricher(graph_store, reader=reader)
    try:
        nodes_written = service.enrich_invoice_context(
            invoice_id,
            {"node_type": payload.node_type, "properties": payload.properties},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return EnrichContextResponse(
        nodes_written=nodes_written,
        invoice_id=invoice_id,
        linked=service.linked,
        warning=service.warning,
    )


def _graph_store(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is not None:
        return graph_store
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


__all__ = ["EnrichContextRequest", "EnrichContextResponse", "router"]
