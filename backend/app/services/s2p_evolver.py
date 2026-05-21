"""S2P wrapper around the SDK PromptVariantEvolver."""

from __future__ import annotations

from typing import Any

from copilot_sdk.evolution import PromptVariantEvolver, VariantSpec

from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG, S2P_VARIANTS


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
    return _s2p_evolver.check_for_promotion()


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


def reset_s2p_evolver() -> None:
    """Reset the SDK evolver and re-register S2P variants."""
    _s2p_evolver.reset()
    _register_initial_variants()


_register_initial_variants()
