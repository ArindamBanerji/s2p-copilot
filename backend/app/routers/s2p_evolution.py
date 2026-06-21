from __future__ import annotations

from typing import Callable, cast

from fastapi import APIRouter, Body, HTTPException, Request

from app.domains.s2p.evolution import S2PEvolutionService
from app.models.responses import GenericResponse, StatusResponse
from app.services.s2p_evolver import (
    check_promotion,
    get_dimensions,
    get_evolution_summary,
    propose_variant,
    reset_s2p_evolver,
)


def create_s2p_evolution_router(
    service_getter: Callable[[Request], S2PEvolutionService] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/s2p/evolution", tags=["S2P Evolution"])

    def service(request: Request) -> S2PEvolutionService:
        if service_getter is not None:
            return service_getter(request)
        state = getattr(request.app, "state", None)
        current = getattr(state, "s2p_evolution", None)
        if current is None:
            raise HTTPException(status_code=500, detail="S2P evolution service is not configured")
        return cast(S2PEvolutionService, current)

    @router.get("/rules", response_model=GenericResponse)
    def rules(request: Request) -> dict:
        return {"rules": service(request).get_rules()}

    @router.get("/variants", response_model=GenericResponse)
    def variants(request: Request, template_name: str | None = None) -> dict:
        rows = service(request).get_variants(template_name)
        return {"total": len(rows), "variants": rows, "sdk_summary": get_evolution_summary()}

    @router.get("/dimensions", response_model=GenericResponse)
    def dimensions() -> dict:
        return {"dimensions": get_dimensions()}

    @router.post("/propose", response_model=GenericResponse)
    def propose(payload: dict = Body(default_factory=dict)) -> dict:
        dimension = payload.get("dimension") if isinstance(payload, dict) else None
        if not dimension:
            raise HTTPException(status_code=400, detail="dimension is required")
        return propose_variant(str(dimension))

    @router.get("/promotion-check", response_model=GenericResponse)
    def promotion_check() -> dict:
        return {"promotion": check_promotion()}

    @router.post("/reset", response_model=StatusResponse)
    def reset() -> dict:
        reset_s2p_evolver()
        return {"status": "reset"}

    @router.get("/shadow-results", response_model=GenericResponse)
    def shadow_results(request: Request, variant_id: str | None = None) -> dict:
        return service(request).get_shadow_results(variant_id)

    @router.get("/promoted", response_model=GenericResponse)
    def promoted(request: Request) -> dict:
        return {"promoted": service(request).get_promoted()}

    return router


router = create_s2p_evolution_router()
