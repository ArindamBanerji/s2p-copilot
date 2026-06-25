"""Optimizer export endpoints for S2P."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.models.responses import GenericResponse
from app.services.optimizer_export import OptimizerExportService
from app.services.supplier_profile_accumulator import accumulator

router = APIRouter(prefix="/api/s2p/optimizer", tags=["s2p-optimizer"])


def _scorer(request: Request) -> Any:
    state = getattr(request.app, "state", None)
    return getattr(state, "scorer", None)


def _profiles() -> list[dict[str, Any]]:
    return [asdict(profile) for profile in accumulator.get_all_profiles()]


def _sections(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


@router.get("/export", response_model=GenericResponse)
def optimizer_export(request: Request, sections: str | None = None) -> dict[str, Any]:
    service = OptimizerExportService()
    try:
        return service.export(
            scorer=_scorer(request),
            profiles=_profiles(),
            sections=_sections(sections),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/schema", response_model=GenericResponse)
def optimizer_schema() -> dict[str, Any]:
    return OptimizerExportService().schema_definition()


@router.get("/validate", response_model=GenericResponse)
def optimizer_validate(request: Request, sections: str | None = None) -> dict[str, Any]:
    service = OptimizerExportService()
    try:
        export = service.export(
            scorer=_scorer(request),
            profiles=_profiles(),
            sections=_sections(sections),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = service.validate(export)
    return {
        **result,
        "version": export["version"],
        "sections_checked": sorted(section for section in export if section in export["sections_available"]),
    }
