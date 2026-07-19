"""Permissive schemas for S2P materialized tab-state keys."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class FlexibleS2PResponse(BaseModel):
    """Allow additive endpoint fields while requiring an object response."""

    model_config = ConfigDict(extra="allow")


class S2PObjectResponse(FlexibleS2PResponse):
    pass


class S2PCollectionResponse(FlexibleS2PResponse):
    total: int | None = None
    count: int | None = None
    source: str | None = None


class S2PLearningGateResponse(FlexibleS2PResponse):
    status: str | None = None
    learning_active: bool | None = None
    thresholds: dict[str, Any] | None = None
