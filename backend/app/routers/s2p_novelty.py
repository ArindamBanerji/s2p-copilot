"""S2P novelty observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.services.novelty_tracker import get_novelty_tracker


router = APIRouter(prefix="/api/s2p/novelty", tags=["s2p-novelty"])


@router.get("/status")
def novelty_status() -> dict:
    return get_novelty_tracker().get_status()


@router.get("/history")
def novelty_history(limit: int = 50) -> dict:
    tracker = get_novelty_tracker()
    return {
        "entries": tracker.get_history(limit=limit),
        "total_in_window": tracker.get_status()["total_in_window"],
        "alert_active": tracker.alert_active,
    }
