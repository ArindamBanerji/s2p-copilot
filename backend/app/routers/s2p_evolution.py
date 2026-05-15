from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, HTTPException, Request

from app.domains.s2p.evolution import S2PEvolutionService


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
        return current

    @router.get("/rules")
    def rules(request: Request) -> dict:
        return {"rules": service(request).get_rules()}

    @router.get("/variants")
    def variants(request: Request, template_name: str | None = None) -> dict:
        rows = service(request).get_variants(template_name)
        return {"total": len(rows), "variants": rows}

    @router.get("/shadow-results")
    def shadow_results(request: Request, variant_id: str | None = None) -> dict:
        return service(request).get_shadow_results(variant_id)

    @router.get("/promoted")
    def promoted(request: Request) -> dict:
        return {"promoted": service(request).get_promoted()}

    return router


router = create_s2p_evolution_router()
