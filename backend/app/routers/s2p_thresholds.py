"""Dollar-aware S2P confidence routing using geometry-derived calibration."""

from __future__ import annotations

from typing import Literal, TypedDict

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator


router = APIRouter(prefix="/api/s2p/confidence", tags=["S2P Confidence"])
CalibrationSource = Literal["geometry_derived"]


class Threshold(TypedDict):
    min_amount: float
    max_amount: float | None
    auto_threshold: float
    calibration: CalibrationSource


class ConfidenceRouteRequest(BaseModel):
    invoice_amount: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    calibration_source: CalibrationSource = "geometry_derived"

    @field_validator("invoice_amount")
    @classmethod
    def finite_amount(cls, value: float) -> float:
        import math

        if not math.isfinite(value):
            raise ValueError("invoice_amount must be finite")
        return value


THRESHOLDS: tuple[Threshold, ...] = (
    {"min_amount": 0.0, "max_amount": 500.0, "auto_threshold": 0.70, "calibration": "geometry_derived"},
    {"min_amount": 500.0, "max_amount": 5000.0, "auto_threshold": 0.80, "calibration": "geometry_derived"},
    {"min_amount": 5000.0, "max_amount": 50000.0, "auto_threshold": 0.85, "calibration": "geometry_derived"},
    {"min_amount": 50000.0, "max_amount": None, "auto_threshold": 0.90, "calibration": "geometry_derived"},
)
K14_NOISE_RATE = 0.92
CALIBRATION_NOTE = (
    "S2P has the highest label noise (92%). Thresholds use geometry-derived "
    "calibration, not surface-label confidence."
)


def _band_for(amount: float) -> Threshold:
    for band in THRESHOLDS:
        maximum = band["max_amount"]
        if amount >= float(band["min_amount"]) and (maximum is None or amount < float(maximum)):
            return band
    return THRESHOLDS[-1]


def _band_label(band: Threshold) -> str:
    minimum = float(band["min_amount"])
    maximum = band["max_amount"]
    if maximum is None:
        return "$50K+"
    if minimum == 0:
        return "$0-$500"
    if maximum == 5000:
        return "$500-$5K"
    return "$5K-$50K"


@router.get("/thresholds")
def get_confidence_thresholds() -> dict[str, object]:
    return {
        "thresholds": list(THRESHOLDS),
        "k14_noise_rate": K14_NOISE_RATE,
        "calibration_source": "geometry_derived",
        "calibration_note": CALIBRATION_NOTE,
    }


@router.post("/route")
def route_confidence(request: ConfidenceRouteRequest) -> dict[str, object]:
    band = _band_for(request.invoice_amount)
    threshold = float(band["auto_threshold"])
    routing = "auto_approve" if request.confidence >= threshold else "manual_review"
    band_label = _band_label(band)
    comparison = "meets" if routing == "auto_approve" else "below"
    return {
        "invoice_amount": request.invoice_amount,
        "confidence": request.confidence,
        "threshold_applied": threshold,
        "routing": routing,
        "reason": f"Confidence {request.confidence:.2f} {comparison} threshold {threshold:.2f} for {band_label} band",
        "calibration_source": request.calibration_source,
        "k14_noise_rate": K14_NOISE_RATE,
    }
