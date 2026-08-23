from __future__ import annotations

from typing import Any, Callable, cast

from fastapi import APIRouter, Body, HTTPException, Request

from copilot_sdk.scoring.mutation_lock import serialize_mutation

from app.domains.s2p.evolution import S2PEvolutionService
from app.models.responses import GenericResponse, StatusResponse
from app.routers.s2p import (
    _current_conservation_status as _score_current_conservation_status,
    _reject_red_write,
    _score_write_governance,
)
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
    @serialize_mutation("s2p", event="evolution")
    def propose(request: Request, payload: dict = Body(default_factory=dict)) -> dict:
        governance = _score_write_governance(request)
        _reject_red_write(governance)
        if governance["conservation_status"] == "AMBER":
            return {"gate": "HELD", "reason": "conservation_amber", "evidence_tier": governance["evidence_tier"]}
        dimension = payload.get("dimension") if isinstance(payload, dict) else None
        if not dimension:
            raise HTTPException(status_code=400, detail="dimension is required")
        return cast(dict[Any, Any], propose_variant(str(dimension)))

    @router.get("/promotion-check", response_model=GenericResponse)
    def promotion_check(request: Request) -> dict:
        # Keep the evolution promotion check anchored to the live provider
        # seam; the shared governance result remains authoritative below.
        live_status = _current_conservation_status(request)
        governance = _score_write_governance(request)
        _reject_red_write(governance)
        if governance["conservation_status"] == "AMBER":
            return {"promotion": None, "gate": "HELD", "reason": "conservation_amber", "evidence_tier": governance["evidence_tier"]}
        promotion = check_promotion(governance["conservation_status"])
        if (
            isinstance(promotion, dict)
            and str(live_status).strip().upper() in {"AMBER", "RED"}
            and promotion.get("reason") == "conservation_gate_unavailable"
        ):
            promotion["reason"] = "conservation_gate_red"
        return {"promotion": promotion, "gate": "PASS", "evidence_tier": governance["evidence_tier"]}

    @router.post("/reset", response_model=StatusResponse)
    @serialize_mutation("s2p", event="reset")
    def reset(request: Request) -> dict:
        governance = _score_write_governance(request)
        _reject_red_write(governance)
        if governance["conservation_status"] == "AMBER":
            return {"status": "HELD", "gate": "HELD", "reason": "conservation_amber", "evidence_tier": governance["evidence_tier"]}
        reset_s2p_evolver()
        return {"status": "reset"}

    @router.get("/shadow-results", response_model=GenericResponse)
    def shadow_results(request: Request, variant_id: str | None = None) -> dict:
        return cast(dict[Any, Any], service(request).get_shadow_results(variant_id))

    @router.get("/promoted", response_model=GenericResponse)
    def promoted(request: Request) -> dict:
        return {"promoted": service(request).get_promoted()}

    return router


router = create_s2p_evolution_router()


def _current_conservation_status(request: Request) -> str:
    """Compatibility seam for callers that observe live evolution state."""
    return _score_current_conservation_status(request)
