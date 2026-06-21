"""S2P wrapper around the SDK PromptVariantEvolver."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from copilot_sdk.evolution import PromptVariantEvolver, VariantSpec

from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG, S2P_VARIANTS
from app.services.s2p_evolution_dimensions import (
    EvolutionDimension,
    S2P_EVOLUTION_DIMENSIONS,
    get_dimension,
)


_INITIAL_VARIANTS: tuple[VariantSpec, ...] = tuple(S2P_VARIANTS)
_s2p_evolver = PromptVariantEvolver(config=S2P_EVOLVER_CONFIG)


def _register_initial_variants() -> None:
    _s2p_evolver.register_variants(list(_INITIAL_VARIANTS))


def _variant_to_dict(spec: VariantSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "family": spec.family,
        "version": spec.version,
        "status": spec.status,
        "metadata": dict(spec.metadata),
    }


def get_active_variant(category: str | None = None, family: str | None = None) -> dict[str, Any] | None:
    """Return the active S2P prompt/rule variant for a category and optional family."""
    if family is not None:
        for spec in _s2p_evolver.store.get_variants_by_family(family):
            if spec.status == "active":
                return _variant_to_dict(spec)
        return None

    spec = _s2p_evolver.get_variant(category=category)
    return _variant_to_dict(spec) if spec is not None else None


def record_triage_outcome(
    variant_id: str,
    reward: float | None = None,
    is_correct: bool | None = None,
    category: str | None = None,
) -> None:
    """Record a triage outcome against the SDK evolver."""
    if is_correct is None:
        if reward is None:
            raise ValueError("Either reward or is_correct must be provided")
        success = float(reward) > 0.0
    else:
        success = bool(is_correct)
    _s2p_evolver.record_outcome(variant_id, success, category=category)


def check_promotion() -> dict | None:
    """Promote a qualifying S2P shadow variant, if one exists."""
    return cast(dict[Any, Any] | None, _s2p_evolver.check_for_promotion())


def get_evolution_summary() -> dict[str, Any]:
    """Return SDK evolver summary with S2P domain context."""
    summary = dict(_s2p_evolver.get_summary())
    variants = summary.get("variants", [])
    families = []
    seen = set()
    if isinstance(variants, list):
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            family = variant.get("family")
            if isinstance(family, str) and family not in seen:
                seen.add(family)
                families.append(family)
    summary["domain"] = "s2p"
    summary["families"] = families
    return summary


def get_registered_variants() -> list[dict[str, Any]]:
    """Return registered variants for tests and diagnostics."""
    return [_variant_to_dict(spec) for spec in _s2p_evolver.store.get_all_variants()]


def get_dimensions() -> list[dict[str, Any]]:
    """Return explicit S2P evolution dimensions for proposal UIs."""
    return [dimension.to_dict() for dimension in S2P_EVOLUTION_DIMENSIONS]


def propose_variant(dimension_name: str) -> dict[str, Any]:
    """Create a deterministic proposal for an S2P evolution dimension."""
    dimension = get_dimension(str(dimension_name or ""))
    if dimension is None:
        return {
            "error": "unknown_dimension",
            "dimension": dimension_name,
            "available_dimensions": [item.name for item in S2P_EVOLUTION_DIMENSIONS],
        }
    proposed_value = _proposal_value(dimension)
    return {
        "variant_id": f"{dimension.name}:{_value_token(proposed_value)}",
        "dimension": dimension.name,
        "parameter_path": dimension.parameter_path,
        "proposed_value": proposed_value,
        "search_space": list(dimension.search_space),
        "metric": dimension.metric,
        "shadow_batch_size": dimension.shadow_batch_size,
        "min_shadow_batches": dimension.min_shadow_batches,
        "status": "proposed",
    }


def shadow_test_variant(variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Read-only shadow evaluation for a proposed S2P evolution variant."""
    candidate = deepcopy(variant if isinstance(variant, dict) else {})
    copied_decisions = deepcopy(decisions if isinstance(decisions, list) else [])
    dimension = get_dimension(str(candidate.get("dimension") or ""))
    if dimension is None:
        return {
            "status": "error",
            "error": "unknown_dimension",
            "dimension": candidate.get("dimension"),
            "decisions_tested": 0,
        }
    if not copied_decisions:
        return {
            "status": "insufficient_data",
            "variant_id": candidate.get("variant_id"),
            "dimension": dimension.name,
            "metric": dimension.metric,
            "decisions_tested": 0,
            "min_shadow_batches": dimension.min_shadow_batches,
            "read_only": True,
        }

    correct = 0
    comparable = 0
    for decision in copied_decisions:
        if not isinstance(decision, dict):
            continue
        actual = str(
            decision.get("ground_truth_action")
            or decision.get("actual_action")
            or decision.get("analyst_action")
            or ""
        )
        recommended = str(decision.get("recommended_action") or decision.get("action") or "")
        if actual:
            comparable += 1
            if recommended == actual:
                correct += 1
    accuracy = (correct / comparable) if comparable else 0.0
    return {
        "status": "completed",
        "variant_id": candidate.get("variant_id"),
        "dimension": dimension.name,
        "metric": dimension.metric,
        "decisions_tested": len(copied_decisions),
        "comparable_decisions": comparable,
        "accuracy": round(accuracy, 4),
        "baseline_accuracy": round(accuracy, 4),
        "regression": False,
        "min_shadow_batches": dimension.min_shadow_batches,
        "read_only": True,
    }


def reset_s2p_evolver() -> None:
    """Reset the SDK evolver and re-register S2P variants."""
    _s2p_evolver.reset()
    _register_initial_variants()


def _proposal_value(dimension: EvolutionDimension) -> float:
    low, high, step = dimension.search_space
    midpoint = low + ((high - low) / 2.0)
    steps = round((midpoint - low) / step)
    proposed = low + (steps * step)
    return round(max(low, min(high, proposed)), 4)


def _value_token(value: float) -> str:
    return str(value).replace(".", "p")


_register_initial_variants()
