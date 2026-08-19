"""S2P Compounding Ledger API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.services.compounding_ledger import CompoundingLedger


def create_ledger_router(ledger: CompoundingLedger) -> APIRouter:
    router = APIRouter(prefix="/api/s2p/ledger", tags=["s2p-ledger"])

    @router.get("/timeline")
    def timeline(limit: int = Query(100, ge=0, le=1000)) -> dict[str, Any]:
        ledger.refresh_live_observations()
        return {"timeline": ledger.timeline(limit), "limit": limit}

    @router.get("/summary")
    def summary() -> dict[str, Any]:
        ledger.refresh_live_observations()
        return ledger.summary()

    @router.get("/iks-trajectory")
    def iks_trajectory(limit: int = Query(100, ge=0, le=1000)) -> dict[str, Any]:
        ledger.refresh_live_observations()
        return {"trajectory": ledger.iks_trajectory(limit), "limit": limit}

    @router.get("/conservation-history")
    def conservation_history(limit: int = Query(100, ge=0, le=1000)) -> dict[str, Any]:
        ledger.refresh_live_observations()
        return {"history": ledger.conservation_history(limit), "limit": limit}

    return router
