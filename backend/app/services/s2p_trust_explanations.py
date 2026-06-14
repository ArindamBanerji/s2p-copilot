"""Trust-weighted S2P evidence explanations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domains.s2p.config import S2PDomainConfig


LEARNING_MESSAGE = "System is learning factor reliability. All factors weighted equally."


@dataclass(frozen=True)
class FactorTrustEvidence:
    name: str
    value: float | None
    dk_weight: float | None
    centroid_mean: float
    distance_from_centroid: float
    contribution: float
    interpretation: str
    display: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "dk_weight": self.dk_weight,
            "centroid_mean": self.centroid_mean,
            "distance_from_centroid": self.distance_from_centroid,
            "contribution": self.contribution,
            "interpretation": self.interpretation,
            "display": self.display,
        }


@dataclass(frozen=True)
class TrustWeightedExplanation:
    category: str
    recommended_action: str
    confidence: float
    phase: str
    trust_available: bool
    factors: list[FactorTrustEvidence]
    summary: str
    learning_message: str | None = None
    verified_count: int | None = None
    verified_target: int | None = 200

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "phase": self.phase,
            "trust_available": self.trust_available,
            "learning_message": self.learning_message,
            "verified_count": self.verified_count,
            "verified_target": self.verified_target,
            "factors": [factor.to_dict() for factor in self.factors],
            "summary": self.summary,
        }


def normalize_factor_values(
    factor_values: dict[str, float] | list[float] | None,
    factor_names: list[str],
) -> dict[str, float | None]:
    if isinstance(factor_values, dict):
        return {name: _safe_float(factor_values.get(name)) for name in factor_names}
    if isinstance(factor_values, list):
        return {
            name: _safe_float(factor_values[index]) if index < len(factor_values) else None
            for index, name in enumerate(factor_names)
        }
    return {name: None for name in factor_names}


def normalize_weights(
    dk_weights: list[list[float]] | list[float] | dict[str, float] | None,
    factor_names: list[str],
    category_index: int | None = None,
    category: str | None = None,
) -> dict[str, float] | None:
    del category
    if dk_weights is None:
        return None
    if isinstance(dk_weights, dict):
        weights = {
            name: value
            for name in factor_names
            if (value := _safe_float(dk_weights.get(name))) is not None
        }
        return weights or None
    if not isinstance(dk_weights, list):
        return None
    if not dk_weights:
        return None

    row: Any
    if all(isinstance(item, list) for item in dk_weights):
        if category_index is None or category_index < 0 or category_index >= len(dk_weights):
            return None
        row = dk_weights[category_index]
    else:
        row = dk_weights

    if not isinstance(row, list):
        return None
    weights = {
        name: value
        for index, name in enumerate(factor_names)
        if index < len(row) and (value := _safe_float(row[index])) is not None
    }
    return weights or None


def normalize_centroid(
    centroid: list[float] | dict[str, float] | None,
    factor_names: list[str],
) -> dict[str, float]:
    if isinstance(centroid, dict):
        return {name: _safe_float(centroid.get(name), 0.5) for name in factor_names}
    if isinstance(centroid, list):
        return {
            name: _safe_float(centroid[index], 0.5) if index < len(centroid) else 0.5
            for index, name in enumerate(factor_names)
        }
    return {name: 0.5 for name in factor_names}


def format_trust_explanation(
    *,
    category: str,
    recommended_action: str,
    confidence: float,
    factor_values: dict[str, float] | list[float] | None,
    factor_names: list[str],
    dk_weights: list[list[float]] | list[float] | dict[str, float] | None,
    centroid: list[float] | dict[str, float] | None = None,
    category_index: int | None = None,
    phase: str | None = None,
    verified_count: int | None = None,
    verified_target: int = 200,
) -> TrustWeightedExplanation:
    names = [name for name in factor_names if name in S2PDomainConfig.factors]
    values = normalize_factor_values(factor_values, names)
    weights = normalize_weights(dk_weights, names, category_index=category_index, category=category)
    trust_available = weights is not None
    centroids = normalize_centroid(centroid if trust_available else None, names)

    factors: list[FactorTrustEvidence] = []
    for name in names:
        value = values.get(name)
        centroid_mean = centroids.get(name, 0.5)
        distance = round(abs((value if value is not None else centroid_mean) - centroid_mean), 6)
        if trust_available:
            weight = weights.get(name) if weights is not None else None
            contribution = round((weight or 0.0) * distance, 6)
            interpretation = _trust_label(weight)
        else:
            weight = None
            contribution = round(distance, 6)
            interpretation = "learning (pre-transition)"
        factors.append(
            FactorTrustEvidence(
                name=name,
                value=value,
                dk_weight=weight,
                centroid_mean=round(centroid_mean, 6),
                distance_from_centroid=distance,
                contribution=contribution,
                interpretation=interpretation,
                display=_display(name, value, weight, interpretation),
            )
        )

    factors.sort(key=lambda factor: (-factor.contribution, factor.name))
    learning_message = None if trust_available else LEARNING_MESSAGE
    summary = _summary(factors, trust_available, learning_message)
    return TrustWeightedExplanation(
        category=category or "unknown",
        recommended_action=recommended_action or "unknown",
        confidence=_safe_float(confidence, 0.0),
        phase=phase or ("variance_learning" if trust_available else "pre_transition"),
        trust_available=trust_available,
        learning_message=learning_message,
        verified_count=verified_count,
        verified_target=verified_target,
        factors=factors,
        summary=summary,
    )


def _trust_label(weight: float | None) -> str:
    if weight is None:
        return "learning (weight unavailable)"
    if weight > 0.7:
        return "trusted factor"
    if weight < 0.3:
        return "noisy factor"
    return "moderate reliability"


def _display(name: str, value: float | None, weight: float | None, interpretation: str) -> str:
    value_text = "unknown" if value is None else f"{value:.3f}"
    if weight is None:
        return f"{name} = {value_text} ({interpretation})"
    return f"{name} = {value_text} ({interpretation}, weight {weight:.2f})"


def _summary(
    factors: list[FactorTrustEvidence],
    trust_available: bool,
    learning_message: str | None,
) -> str:
    if not factors:
        return learning_message or "No factor values were available for trust-weighted explanation."
    top = factors[:2]
    names = " and ".join(factor.name for factor in top)
    if trust_available:
        return f"Top trust-weighted contributors: {names}."
    return f"{learning_message} Largest neutral-distance factors: {names}."


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
