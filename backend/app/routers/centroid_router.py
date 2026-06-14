"""Read-only S2P centroid explorer API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.domains.s2p.config import S2PDomainConfig
from app.services.centroid_explorer import CentroidExplorerError, S2PCentroidExplorerService


router = APIRouter(prefix="/api/s2p/centroid", tags=["s2p-centroid"])


@router.get("/all")
def all_centroids(request: Request) -> dict[str, Any]:
    service = _service(request)
    try:
        cells = [cell.to_dict() for cell in service.get_all_centroid_cells()]
    except CentroidExplorerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return {
        "cells": cells,
        "shape": {
            "categories": S2PDomainConfig.n_categories,
            "actions": S2PDomainConfig.n_actions,
            "factors": S2PDomainConfig.n_factors,
        },
        "categories": list(S2PDomainConfig.categories),
        "actions": list(S2PDomainConfig.actions),
        "factors": list(S2PDomainConfig.factors),
        "read_only": True,
    }


@router.get("/explain/{decision_id}")
def explain_decision(decision_id: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).explain_decision(decision_id).to_dict()
    except CentroidExplorerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/drift/{category}/{action}")
def centroid_drift(category: str, action: str, request: Request, limit: int = 50) -> dict[str, Any]:
    try:
        return _service(request).get_centroid_drift(category, action, limit=limit).to_dict()
    except CentroidExplorerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{category}/{action}")
def centroid_cell(category: str, action: str, request: Request) -> dict[str, Any]:
    try:
        return _service(request).get_centroid_cell(category, action).to_dict()
    except CentroidExplorerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _service(request: Request) -> S2PCentroidExplorerService:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        raise HTTPException(status_code=503, detail="S2P scorer is not configured")
    graph_store = getattr(state, "graph_store", None) or getattr(scorer, "graph_store", None)
    return S2PCentroidExplorerService(scorer=scorer, graph_store=graph_store)
