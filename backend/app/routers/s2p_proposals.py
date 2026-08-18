"""S2P Decision-Change Proposal API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.proposal_service import ProposalService


class ProposalCreateRequest(BaseModel):
    invoice_id: str
    score_result: dict[str, Any]
    factor_vector: list[float]
    evidence: dict[str, Any] = Field(default_factory=dict)


class ProposalOverrideRequest(BaseModel):
    override_action: str
    reason: str


def create_proposal_router(service: ProposalService) -> APIRouter:
    router = APIRouter(prefix="/api/s2p", tags=["S2P proposals"])

    @router.post("/proposal")
    def create_proposal(request: ProposalCreateRequest) -> dict[str, Any]:
        try:
            return service.create_from_score(
                request.score_result,
                request.invoice_id,
                request.factor_vector,
                request.evidence,
            ).to_dict()
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/proposal/{proposal_id}")
    def get_proposal(proposal_id: str) -> dict[str, Any]:
        proposal = service.store.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        payload = proposal.to_dict()
        payload["outcome"] = service.store.get_outcome(proposal_id)
        payload["audit_trail"] = service.get_audit_trail(proposal.invoice_id)
        return payload

    @router.get("/proposals")
    def list_proposals(
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        proposals = service.store.list_recent(limit)
        if status is not None:
            proposals = [proposal for proposal in proposals if proposal.status == status]
        return {"count": len(proposals), "proposals": [proposal.to_dict() for proposal in proposals]}

    @router.post("/proposal/{proposal_id}/confirm")
    def confirm_proposal(proposal_id: str) -> dict[str, Any]:
        try:
            return service.confirm(proposal_id).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/proposal/{proposal_id}/override")
    def override_proposal(
        proposal_id: str,
        request: ProposalOverrideRequest = Body(...),
    ) -> dict[str, Any]:
        try:
            return service.override(proposal_id, request.override_action, request.reason).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/proposal/{proposal_id}/audit")
    def audit_proposal(proposal_id: str) -> dict[str, Any]:
        proposal = service.store.get(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return {
            "proposal_id": proposal_id,
            "invoice_id": proposal.invoice_id,
            "audit_trail": service.get_audit_trail(proposal.invoice_id),
        }

    return router
