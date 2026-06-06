"""S2P-specific AgentEvolver dimension definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvolutionDimension:
    name: str
    parameter_path: str
    search_space: tuple[float, float, float]
    metric: str
    shadow_batch_size: int
    min_shadow_batches: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parameter_path": self.parameter_path,
            "search_space": list(self.search_space),
            "metric": self.metric,
            "shadow_batch_size": self.shadow_batch_size,
            "min_shadow_batches": self.min_shadow_batches,
        }


S2P_EVOLUTION_DIMENSIONS: tuple[EvolutionDimension, ...] = (
    EvolutionDimension(
        name="auto_approve_threshold_price_variance",
        parameter_path="routing.auto_approve.thresholds.price_variance",
        search_space=(0.78, 0.96, 0.03),
        metric="safe_auto_approve_rate",
        shadow_batch_size=25,
        min_shadow_batches=3,
    ),
    EvolutionDimension(
        name="auto_approve_threshold_quantity_mismatch",
        parameter_path="routing.auto_approve.thresholds.quantity_mismatch",
        search_space=(0.76, 0.94, 0.03),
        metric="safe_auto_approve_rate",
        shadow_batch_size=25,
        min_shadow_batches=3,
    ),
    EvolutionDimension(
        name="supplier_trust_weight",
        parameter_path="scoring.weights.supplier_exception_history",
        search_space=(0.10, 0.40, 0.05),
        metric="supplier_exception_precision",
        shadow_batch_size=30,
        min_shadow_batches=3,
    ),
    EvolutionDimension(
        name="factor_importance_price_variance",
        parameter_path="scoring.category_weights.price_variance.amount_variance_ratio",
        search_space=(0.15, 0.45, 0.05),
        metric="category_weighted_accuracy",
        shadow_batch_size=30,
        min_shadow_batches=3,
    ),
    EvolutionDimension(
        name="escalation_amount_threshold",
        parameter_path="routing.escalation.amount_threshold",
        search_space=(25000.0, 100000.0, 25000.0),
        metric="high_value_capture_rate",
        shadow_batch_size=20,
        min_shadow_batches=3,
    ),
    EvolutionDimension(
        name="batch_processing_threshold",
        parameter_path="operations.batch.min_invoice_count",
        search_space=(10.0, 100.0, 10.0),
        metric="batch_processing_precision",
        shadow_batch_size=20,
        min_shadow_batches=3,
    ),
)


def get_dimension(name: str) -> EvolutionDimension | None:
    for dimension in S2P_EVOLUTION_DIMENSIONS:
        if dimension.name == name:
            return dimension
    return None


__all__ = [
    "EvolutionDimension",
    "S2P_EVOLUTION_DIMENSIONS",
    "get_dimension",
]
