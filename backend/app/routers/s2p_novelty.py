"""S2P novelty observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.domains.s2p.config import S2PDomainConfig
from app.services.novelty_tracker import get_novelty_tracker


router = APIRouter(prefix="/api/s2p/novelty", tags=["s2p-novelty"])
NOVELTY_THRESHOLD = 0.20
AUTO_PAUSE_THRESHOLD = 0.30


def _novelty_status_for_rate(rate: float) -> str:
    if rate >= AUTO_PAUSE_THRESHOLD:
        return "RED"
    if rate >= NOVELTY_THRESHOLD:
        return "AMBER"
    return "GREEN"


def _category_rate_rows() -> list[dict]:
    tracker_status = get_novelty_tracker().get_status()
    breakdown = tracker_status.get("per_category", {})
    rows: list[dict] = []
    for index, name in enumerate(S2PDomainConfig.categories):
        category_stats = breakdown.get(name, {})
        rate = float(category_stats.get("novelty_rate", 0.0) or 0.0)
        rows.append(
            {
                "category": index,
                "name": name,
                "novelty_rate": rate,
                "status": _novelty_status_for_rate(rate),
            }
        )
    return rows


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


@router.get("/rate")
def novelty_rate() -> dict:
    tracker = get_novelty_tracker()
    overall_rate = float(tracker.novelty_rate)
    return {
        "categories": _category_rate_rows(),
        "overall_rate": overall_rate,
        "overall_status": _novelty_status_for_rate(overall_rate),
        "threshold": NOVELTY_THRESHOLD,
        "auto_pause_threshold": AUTO_PAUSE_THRESHOLD,
    }


@router.get("/auto-pause")
def novelty_auto_pause() -> dict:
    paused_categories = [
        {
            "category": row["category"],
            "name": row["name"],
            "novelty_rate": row["novelty_rate"],
            "reason": "Exceeds 30% threshold",
        }
        for row in _category_rate_rows()
        if row["novelty_rate"] >= AUTO_PAUSE_THRESHOLD
    ]
    return {
        "paused_categories": paused_categories,
        "advisory_only": True,
    }
