"""S2P supplier enrichment endpoints backed by GraphStore entity enrichment."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.graph.s2p_graph_reader import S2PGraphReader
from app.services.s2p_enrichment import NAMESPACE, S2PSupplierEnrichmentService

router = APIRouter(prefix="/api/s2p/enrichment", tags=["s2p-enrichment"])


class EnrichmentRunResponse(BaseModel):
    report: dict[str, Any]
    receipts: list[dict[str, Any]]
    suppliers: list[dict[str, Any]]


class EnrichedSupplierResponse(BaseModel):
    supplier_id: str
    namespace: str
    metrics: dict[str, dict[str, Any]]
    enrichment_shown: bool = True


class EnrichmentSummaryResponse(BaseModel):
    suppliers: list[dict[str, Any]]
    total_suppliers: int
    namespace: str


class EnrichmentAlertsResponse(BaseModel):
    alerts: list[dict[str, Any]]
    total_alerts: int
    threshold: float


@router.post("/run", response_model=EnrichmentRunResponse)
def run_supplier_enrichment(
    request: Request,
    dry_run: bool = Query(True),
    min_decisions: int = Query(20, ge=0),
) -> EnrichmentRunResponse:
    service = _service(request)
    return EnrichmentRunResponse(**service.run(dry_run=dry_run, min_decisions=min_decisions))


@router.get("/summary", response_model=EnrichmentSummaryResponse)
def enrichment_summary(request: Request) -> EnrichmentSummaryResponse:
    service = _service(request)
    suppliers = service.summary()
    return EnrichmentSummaryResponse(
        suppliers=suppliers,
        total_suppliers=len(suppliers),
        namespace=NAMESPACE,
    )


@router.get("/alerts", response_model=EnrichmentAlertsResponse)
def enrichment_alerts(
    request: Request,
    threshold: float = Query(0.10, ge=0.0),
) -> EnrichmentAlertsResponse:
    service = _service(request)
    alerts = service.alerts(threshold=threshold)
    return EnrichmentAlertsResponse(
        alerts=alerts,
        total_alerts=len(alerts),
        threshold=float(threshold),
    )


@router.get("/supplier/{supplier_id}", response_model=EnrichedSupplierResponse)
def enrichment_supplier(request: Request, supplier_id: str) -> EnrichedSupplierResponse:
    service = _service(request)
    metrics = service.read_supplier(supplier_id)
    if not metrics:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} enrichment not found")
    return EnrichedSupplierResponse(
        supplier_id=supplier_id,
        namespace=NAMESPACE,
        metrics=service.serialize_values(metrics),
        enrichment_shown=True,
    )


def _service(request: Request) -> S2PSupplierEnrichmentService:
    state = getattr(request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is None:
        scorer = getattr(state, "scorer", None)
        graph_store = getattr(scorer, "graph_store", None)
    if graph_store is None:
        raise HTTPException(status_code=503, detail="GraphStore unavailable for S2P enrichment")
    scorer = getattr(state, "scorer", None)
    scorer_store = getattr(scorer, "graph_store", None)
    reader = getattr(state, "s2p_graph_reader", None)
    if not isinstance(reader, S2PGraphReader) or reader.store is not scorer_store:
        if scorer_store is None:
            raise HTTPException(status_code=503, detail="S2P graph reader unavailable")
        reader = S2PGraphReader(store=scorer_store)
    return S2PSupplierEnrichmentService(graph_store=graph_store, reader=reader)


__all__ = [
    "EnrichmentAlertsResponse",
    "EnrichmentRunResponse",
    "EnrichmentSummaryResponse",
    "EnrichedSupplierResponse",
    "router",
]
