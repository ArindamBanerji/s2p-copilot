"""S2P supplier enrichment endpoints backed by GraphStore entity enrichment."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.graph.s2p_graph_reader import S2PGraphReader
from app.models.responses import GenericResponse
from app.services.response_materializer import cached_response
from app.services.s2p_enrichment import ENRICHMENT_READ_ERRORS, NAMESPACE, S2PSupplierEnrichmentService
from app.services.supplier_profile_provider import build_profiles

router = APIRouter(prefix="/api/s2p/enrichment", tags=["s2p-enrichment"])
compat_router = APIRouter(prefix="/api/s2p", tags=["s2p-enrichment"])


class EnrichmentRunResponse(BaseModel):
    report: dict[str, Any]
    receipts: list[dict[str, Any]]
    suppliers: list[dict[str, Any]]


class EnrichedSupplierResponse(BaseModel):
    supplier_id: str
    namespace: str
    metrics: dict[str, dict[str, Any]]
    enrichment_shown: bool = True
    graph_available: bool = True


class EnrichmentSummaryResponse(BaseModel):
    suppliers: list[dict[str, Any]]
    total_suppliers: int
    namespace: str
    data_available: bool = True


class EnrichmentAlertsResponse(BaseModel):
    alerts: list[dict[str, Any]]
    total_alerts: int
    threshold: float
    alerts_available: bool = True


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
    summary_result = service.summary(return_status=True)
    suppliers, data_available = cast(tuple[list[dict[str, Any]], bool], summary_result)
    return EnrichmentSummaryResponse(
        suppliers=suppliers,
        total_suppliers=len(suppliers),
        namespace=NAMESPACE,
        data_available=data_available,
    )


@router.get("/alerts", response_model=EnrichmentAlertsResponse)
def enrichment_alerts(
    request: Request,
    threshold: float = Query(0.10, ge=0.0),
) -> EnrichmentAlertsResponse:
    alerts: list[dict[str, Any]] = []
    alerts_available = False
    try:
        service = _service(request)
        result = service.alerts(threshold=threshold) if isinstance(service, S2PSupplierEnrichmentService) else None
        if isinstance(result, list) and all(isinstance(alert, dict) for alert in result):
            alerts = result
            alerts_available = True
    except ENRICHMENT_READ_ERRORS:
        pass
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
    return EnrichmentAlertsResponse(
        alerts=alerts,
        total_alerts=len(alerts),
        threshold=float(threshold),
        alerts_available=alerts_available,
    )


@router.get("/supplier/{supplier_id}", response_model=EnrichedSupplierResponse)
def enrichment_supplier(request: Request, supplier_id: str) -> EnrichedSupplierResponse:
    service = _service(request)
    read_result = service.read_supplier(supplier_id, return_status=True)
    metrics, graph_available = cast(
        tuple[dict[str, Any] | None, bool], read_result,
    )
    if not graph_available:
        return EnrichedSupplierResponse(
            supplier_id=supplier_id,
            namespace=NAMESPACE,
            metrics={},
            enrichment_shown=False,
            graph_available=False,
        )
    if not metrics:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} enrichment not found")
    return EnrichedSupplierResponse(
        supplier_id=supplier_id,
        namespace=NAMESPACE,
        metrics=service.serialize_values(cast(dict[str, Any], metrics)),
        enrichment_shown=True,
    )


@compat_router.get("/supplier-enrichment/{supplier_id}", response_model=GenericResponse)
def supplier_enrichment(supplier_id: str, request: Request) -> dict[str, Any]:
    profiles = cached_response(request, "supplier_profiles", item="profiles")
    if not isinstance(profiles, list):
        profiles = build_profiles([])
    profile = next(
        (
            row
            for row in profiles
            if isinstance(row, dict) and row.get("supplier_id") == supplier_id
        ),
        None,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    return cast(dict[str, Any], profile)


def _service(request: Request) -> S2PSupplierEnrichmentService:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    scorer_store = getattr(scorer, "graph_store", None)
    configured_graph_store = getattr(state, "graph_store", None)
    # Tests and isolated app instances may replace graph_store after startup;
    # honor that override instead of leaking the process-level fallback store.
    if configured_graph_store is not None and configured_graph_store is not scorer_store:
        graph_store = configured_graph_store
    else:
        graph_store = getattr(state, "enrichment_store", None) or configured_graph_store or scorer_store
    if graph_store is None:
        raise HTTPException(status_code=503, detail="GraphStore unavailable for S2P enrichment")
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
    "compat_router",
    "router",
]
