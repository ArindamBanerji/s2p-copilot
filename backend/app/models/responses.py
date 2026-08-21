"""Flexible Pydantic response models for S2P OpenAPI schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class FlexibleResponse(BaseModel):
    """Base response model that preserves existing additive response fields."""

    model_config = ConfigDict(extra="allow")


class GenericResponse(FlexibleResponse):
    pass


class S2PScoreResponse(FlexibleResponse):
    event_id: str | None = None
    category: str | None = None
    action: str | None = None
    action_index: int | None = None
    confidence: float | None = None
    probabilities: list[float] | None = None
    factor_vector: list[float] | None = None
    factor_names: list[str] | None = None
    decision_id: str | None = None
    proposal_id: str | None = None
    process_context: dict[str, Any] | None = None
    active_variant: dict[str, Any] | None = None
    auto_approve: dict[str, Any] | None = None
    novelty_score: float | None = None
    threshold_decision: dict[str, Any] | None = None
    gate: str | None = None
    conservation_status: str | None = None
    evidence_tier: str | None = None
    learning_applied: bool | None = None
    reason: str | None = None


class LearningGateResponse(FlexibleResponse):
    status: str | None = None
    learning_active: bool | None = None
    verified_decisions: int | None = None
    override_precision: float | None = None
    sigma_max: float | None = None
    reason: str | None = None
    recommendation: str | None = None
    gate_opened_at: str | None = None
    thresholds: dict[str, Any] | None = None


class CollectionResponse(FlexibleResponse):
    total: int | None = None
    count: int | None = None
    source: str | None = None


class StatusResponse(FlexibleResponse):
    status: str | None = None


class ExplorerCentroidResponse(FlexibleResponse):
    category: int | None = None
    category_name: str | None = None
    action: int | None = None
    action_name: str | None = None
    centroid: list[float] | None = None
    factors: list[str] | None = None


class FinancialImpactResponse(FlexibleResponse):
    total_recovered: float | None = None
    total_at_risk: float | None = None
    total_leakage_prevented: float | None = None
    by_category: dict[str, Any] | None = None
    auto_approve_savings_hours: float | None = None
    source: str | None = None


__all__ = [
    "CollectionResponse",
    "ExplorerCentroidResponse",
    "FinancialImpactResponse",
    "FlexibleResponse",
    "GenericResponse",
    "LearningGateResponse",
    "S2PScoreResponse",
    "StatusResponse",
]
