"""S2P novelty observability endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.domains.s2p.config import S2PDomainConfig
from app.models.responses import GenericResponse
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
                "conservation_review": rate >= NOVELTY_THRESHOLD,
                "recommendation": _review_recommendation(name, rate),
            }
        )
    return rows


@router.get("/status", response_model=GenericResponse)
def novelty_status() -> dict:
    status = get_novelty_tracker().get_status()
    rate = float(status.get("novelty_rate", 0.0) or 0.0)
    categories = _category_rate_rows()
    review_categories = [row for row in categories if row["conservation_review"]]
    top_status = _top_level_status(rate, categories)
    return {
        **status,
        "status": top_status,
        "conservation_review": bool(review_categories),
        "review_categories": review_categories,
        "recommendation": (
            "Review category conservation. Novelty rate "
            f"{round(rate * 100)}%."
            if review_categories
            else ""
        ),
    }


@router.get("/history", response_model=GenericResponse)
def novelty_history(limit: int = 50) -> dict:
    tracker = get_novelty_tracker()
    return {
        "entries": tracker.get_history(limit=limit),
        "total_in_window": tracker.get_status()["total_in_window"],
        "alert_active": tracker.alert_active,
    }


@router.get("/rate", response_model=GenericResponse)
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


@router.get("/auto-pause", response_model=GenericResponse)
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


@router.get("/triggered-decisions", response_model=GenericResponse)
def novelty_triggered_decisions(limit: int = 50) -> dict:
    tracker = get_novelty_tracker()
    return {
        "decisions": tracker.get_triggered_decisions(limit=limit),
        "total": len(tracker.get_triggered_decisions(limit=limit)),
    }


def _review_recommendation(category: str, rate: float) -> str:
    if rate < NOVELTY_THRESHOLD:
        return ""
    return f"Review {category} conservation. Novelty rate {round(rate * 100)}%."


def _top_level_status(overall_rate: float, categories: list[dict]) -> str:
    statuses = {str(row.get("status") or "") for row in categories}
    if "RED" in statuses:
        return "RED"
    if "AMBER" in statuses:
        return "AMBER"
    return _novelty_status_for_rate(overall_rate)
