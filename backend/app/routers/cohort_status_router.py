"""S2P cohort status API."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter

from copilot_sdk.state.cached_static import cached_static

from app.services.cohort_status import CohortStatusService


def create_cohort_status_router(
    graph_store_factory: Callable[[], Any] | None = None,
    oracle_artifact_path: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/s2p", tags=["cohort-status"])

    @router.get("/cohort-status")
    @cached_static("cohort-status", copilot="s2p")
    def get_cohort_status() -> dict[str, Any]:
        store = graph_store_factory() if graph_store_factory is not None else None
        return CohortStatusService(
            graph_store=store,
            oracle_artifact_path=oracle_artifact_path,
        ).get_status()

    return router
