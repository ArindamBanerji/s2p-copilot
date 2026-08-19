"""S2P promotion and Frozen Twin API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.s2p_autonomy import S2PAutonomyManager


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
    def advance(category: str, request: AdvanceRequest) -> dict[str, Any]:
        try:
            return manager.advance(category, request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/promotion/{category}/rollback")
    def rollback(category: str, request: RollbackRequest) -> dict[str, Any]:
        try:
            return manager.rollback(category, request.reason)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/promotion/{category}/transfer")
    def transfer(category: str, request: TransferRequest) -> dict[str, Any]:
        try:
            return manager.transfer(category, request.target_category, request.evidence)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/twin/status")
    def twin_status() -> dict[str, Any]:
        return manager.twin_status()

    @router.get("/twin/drift")
    def twin_drift() -> dict[str, Any]:
        try:
            return manager.drift()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/twin/freeze")
    def twin_freeze() -> dict[str, Any]:
        try:
            return manager.freeze()
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RuntimeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"Frozen Twin unavailable: {exc}") from exc

    return router
