"""S2P promotion and Frozen Twin API."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.s2p_autonomy import S2PAutonomyManager
from app.routers.s2p import _reject_red_write, _score_write_governance


class AdvanceRequest(BaseModel):
    shadow_decisions: int = 0
    measurement_decisions: int = 0
    improvement: float = 0.0
    conservation_state: str | None = None
    evidence_tier: str | None = None
    reason: str = "api_advance"


class RollbackRequest(BaseModel):
    reason: str = "operator_rollback"


class TransferRequest(BaseModel):
    target_category: str
    evidence: dict[str, Any] = Field(default_factory=dict)


def create_s2p_autonomy_router(manager: S2PAutonomyManager) -> APIRouter:
    router = APIRouter(prefix="/api/s2p", tags=["s2p-autonomy"])

    @router.get("/promotion/status")
    def promotion_status() -> dict[str, Any]:
        return {"categories": manager.statuses(), "evidence_tier": manager.evidence_tier()}

    @router.post("/promotion/{category}/advance")
    def advance(category: str, request: AdvanceRequest, http_request: Request) -> dict[str, Any]:
        try:
            governance = _score_write_governance(http_request)
            _reject_red_write(governance)
            if governance["conservation_status"] == "AMBER":
                return {"category": category, "advanced": False, "gate": "HELD", "reason": "conservation_amber", "evidence_tier": governance["evidence_tier"]}
            result = cast(dict[str, Any], manager.advance(category, request.model_dump()))
            result["evidence_tier"] = governance["evidence_tier"]
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/promotion/{category}/rollback")
    def rollback(category: str, request: RollbackRequest, http_request: Request) -> dict[str, Any]:
        try:
            governance = _score_write_governance(http_request)
            _reject_red_write(governance)
            if governance["conservation_status"] == "AMBER":
                return {"category": category, "rolled_back": False, "gate": "HELD", "reason": "conservation_amber", "evidence_tier": governance["evidence_tier"]}
            result = cast(dict[str, Any], manager.rollback(category, request.reason))
            result["evidence_tier"] = governance["evidence_tier"]
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/promotion/{category}/transfer")
    def transfer(category: str, request: TransferRequest, http_request: Request) -> dict[str, Any]:
        try:
            governance = _score_write_governance(http_request)
            _reject_red_write(governance)
            if governance["conservation_status"] == "AMBER":
                return {"category": category, "transferred": False, "gate": "HELD", "reason": "conservation_amber", "evidence_tier": governance["evidence_tier"]}
            result = cast(dict[str, Any], manager.transfer(category, request.target_category, request.evidence))
            result["evidence_tier"] = governance["evidence_tier"]
            return result
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/twin/status")
    def twin_status() -> dict[str, Any]:
        return cast(dict[str, Any], manager.twin_status())

    @router.get("/twin/drift")
    def twin_drift() -> dict[str, Any]:
        try:
            return cast(dict[str, Any], manager.drift())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/twin/freeze")
    def twin_freeze() -> dict[str, Any]:
        try:
            return cast(dict[str, Any], manager.freeze())
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"Frozen Twin unavailable: {exc}") from exc

    return router
