"""P40B shadow-only auto-approve API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from copilot_sdk.scoring.mutation_lock import serialize_mutation
from copilot_sdk.state.cached_static import cached_static

from app.graph.s2p_graph_reader import S2PGraphReader
from app.routers.s2p import _current_conservation_status
from app.services.s2p_auto_approve_gate import gate


router = APIRouter(prefix="/api/s2p/auto-approve", tags=["S2P Auto Approve Shadow"])


def _graph_store(request: Request) -> Any | None:
    graph_store = getattr(request.app.state, "graph_store", None)
    if graph_store is not None:
        return graph_store
    scorer = getattr(request.app.state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _graph_reader(request: Request) -> S2PGraphReader | None:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    scorer_store = getattr(scorer, "graph_store", None)
    reader = getattr(state, "s2p_graph_reader", None)
    if isinstance(reader, S2PGraphReader) and reader.store is scorer_store:
        return reader
    if scorer_store is None:
        return None
    return S2PGraphReader(store=scorer_store)


class EnableRequest(BaseModel):
    mode: str = "shadow"
    min_verified_decisions: int | None = None
    initial_threshold: float | None = None
    min_threshold: float | None = None
    spot_check_rate: float | None = None
    random_seed: int | None = None


class EvaluateRequest(BaseModel):
    category: str
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: str
    decision_id: str | None = None
    invoice_id: str | None = None
    supplier_id: str | None = None


@router.get("/status")
@cached_static("auto-approve-status", copilot="s2p")
def auto_approve_status(request: Request) -> dict[str, Any]:
    conservation_status = _current_conservation_status(request)
    return gate.status_by_category(
        graph_store=_graph_store(request),
        reader=_graph_reader(request),
        conservation_status=conservation_status,
    )


@router.post("/enable")
@serialize_mutation("s2p", event="reset")
def enable_auto_approve(request: EnableRequest) -> dict[str, Any]:
    if request.mode not in {"shadow"}:
        raise HTTPException(
            status_code=400,
            detail="P40B supports shadow mode only; execution and assistive modes are deferred.",
        )
    updates: dict[str, Any] = {}
    for key in ("min_verified_decisions", "initial_threshold", "min_threshold", "spot_check_rate", "random_seed"):
        value = getattr(request, key)
        if value is not None:
            updates[key] = value
    try:
        config = gate.configure(enabled=True, mode="shadow", **updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "enabled": True,
        "mode": "shadow",
        "config": config,
        "execution_authority": False,
        "message": "P40B shadow gate enabled; no outcomes or learning are written.",
    }


@router.post("/disable")
@serialize_mutation("s2p", event="reset")
def disable_auto_approve() -> dict[str, Any]:
    config = gate.disable()
    return {
        "enabled": False,
        "mode": "disabled",
        "config": config,
        "execution_authority": False,
        "message": "P40B shadow gate disabled.",
    }


@router.get("/audit")
@cached_static("auto-approve-audit", copilot="s2p")
def auto_approve_audit() -> dict[str, Any]:
    events = gate.audit_log()
    return {
        "shadow_evaluation_log": events,
        "total_events": len(events),
        "durable_audit": False,
        "learning_applied": False,
        "outcome_written": False,
    }


@router.post("/evaluate")
@serialize_mutation("s2p", event="score")
def evaluate_auto_approve(request: EvaluateRequest, http_request: Request) -> dict[str, Any]:
    conservation_status = _current_conservation_status(http_request)
    return gate.evaluate(
        category=request.category,
        confidence=request.confidence,
        recommended_action=request.recommended_action,
        decision_id=request.decision_id,
        invoice_id=request.invoice_id,
        supplier_id=request.supplier_id,
        graph_store=_graph_store(http_request),
        reader=_graph_reader(http_request),
        conservation_status=conservation_status,
    )
