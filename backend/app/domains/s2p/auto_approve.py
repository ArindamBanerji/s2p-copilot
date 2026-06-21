"""S2P auto-approve confidence gate and demo telemetry."""

from __future__ import annotations

from copy import deepcopy
import random
from typing import Any, Callable


AUTO_APPROVE_THRESHOLDS = {
    "price_variance": 0.90,
    "quantity_mismatch": 0.85,
    "duplicate_risk": 0.92,
    "contract_gap": 0.88,
    "format_compliance": 0.80,
}

SPOT_CHECK_RATE = 0.02
AUTO_APPROVE_ACTION = "auto_approve"
MIN_EXPANSION_VERIFIED_DECISIONS = 20
MIN_EXPANSION_ACCURACY = 0.95


def _default_spot_check() -> bool:
    return random.random() < SPOT_CHECK_RATE


def _should_auto_approve(
    category: str,
    confidence: float,
    conservation_status: str,
    recommended_action: str,
    spot_check_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    threshold = AUTO_APPROVE_THRESHOLDS.get(category)
    if threshold is None:
        return {
            "auto_approved": False,
            "reason": "unknown_category",
            "threshold": None,
            "spot_check": False,
            "category": category,
        }

    if confidence < threshold:
        return {
            "auto_approved": False,
            "reason": "below_threshold",
            "threshold": threshold,
            "spot_check": False,
            "category": category,
        }

    if conservation_status != "GREEN":
        return {
            "auto_approved": False,
            "reason": "conservation_not_green",
            "threshold": threshold,
            "spot_check": False,
            "category": category,
        }

    if recommended_action != AUTO_APPROVE_ACTION:
        return {
            "auto_approved": False,
            "reason": "wrong_action",
            "threshold": threshold,
            "spot_check": False,
            "category": category,
        }

    spot_check = bool((spot_check_fn or _default_spot_check)())
    if spot_check:
        return {
            "auto_approved": False,
            "reason": "spot_check",
            "threshold": threshold,
            "spot_check": True,
            "category": category,
        }

    return {
        "auto_approved": True,
        "reason": "approved",
        "threshold": threshold,
        "spot_check": False,
        "category": category,
    }


def _initial_category_stats() -> dict[str, dict[str, int | float]]:
    return {
        category: {"approved": 0, "held": 0, "threshold": threshold}
        for category, threshold in AUTO_APPROVE_THRESHOLDS.items()
    }


_stats: dict[str, Any] = {
    "total_auto_approved": 0,
    "total_spot_checked": 0,
    "spot_check_correct": 0,
    "total_decisions": 0,
    "per_category": _initial_category_stats(),
}


def reset_auto_approve_stats() -> None:
    _stats["total_auto_approved"] = 0
    _stats["total_spot_checked"] = 0
    _stats["spot_check_correct"] = 0
    _stats["total_decisions"] = 0
    _stats["per_category"] = _initial_category_stats()


def record_auto_approve_decision(decision: dict[str, Any]) -> None:
    category = str(decision.get("category") or "")
    category_stats = _stats["per_category"].setdefault(
        category,
        {"approved": 0, "held": 0, "threshold": AUTO_APPROVE_THRESHOLDS.get(category)},
    )
    _stats["total_decisions"] += 1
    if decision.get("auto_approved"):
        _stats["total_auto_approved"] += 1
        category_stats["approved"] += 1
    else:
        category_stats["held"] += 1
    if decision.get("spot_check"):
        _stats["total_spot_checked"] += 1


def get_auto_approve_stats() -> dict[str, Any]:
    total_spot_checked = int(_stats["total_spot_checked"])
    total_decisions = int(_stats["total_decisions"])
    return {
        "total_auto_approved": int(_stats["total_auto_approved"]),
        "total_spot_checked": total_spot_checked,
        "spot_check_accuracy": (
            float(_stats["spot_check_correct"]) / total_spot_checked
            if total_spot_checked > 0
            else 0.0
        ),
        "per_category": deepcopy(_stats["per_category"]),
        "current_auto_approve_rate": (
            float(_stats["total_auto_approved"]) / total_decisions
            if total_decisions > 0
            else 0.0
        ),
        "source": "in_memory_demo_stats",
    }


def build_expansion_proof(
    category: str,
    *,
    verified_decisions: int,
    correct_decisions: int,
    conservation_status: str,
) -> dict[str, Any]:
    threshold = AUTO_APPROVE_THRESHOLDS.get(category)
    if threshold is None:
        raise KeyError(category)

    accuracy = (
        float(correct_decisions) / verified_decisions if verified_decisions > 0 else 0.0
    )
    proposed_threshold = max(0.0, round(threshold - 0.05, 4))
    safe_to_expand = (
        verified_decisions >= MIN_EXPANSION_VERIFIED_DECISIONS
        and accuracy >= MIN_EXPANSION_ACCURACY
        and conservation_status == "GREEN"
    )
    if safe_to_expand:
        evidence = (
            f"{category} has {verified_decisions} verified decisions, "
            f"{accuracy:.1%} accuracy, and GREEN conservation."
        )
    elif verified_decisions < MIN_EXPANSION_VERIFIED_DECISIONS:
        evidence = (
            f"{category} has {verified_decisions} verified decisions; "
            f"{MIN_EXPANSION_VERIFIED_DECISIONS} are required."
        )
    elif accuracy < MIN_EXPANSION_ACCURACY:
        evidence = (
            f"{category} accuracy is {accuracy:.1%}; "
            f"{MIN_EXPANSION_ACCURACY:.0%} is required."
        )
    else:
        evidence = f"{category} conservation status is {conservation_status}, not GREEN."

    return {
        "category": category,
        "current_threshold": threshold,
        "proposed_threshold": proposed_threshold,
        "verified_decisions": verified_decisions,
        "accuracy": accuracy,
        "conservation_status": conservation_status,
        "safe_to_expand": safe_to_expand,
        "evidence": evidence,
        "rollback_available": True,
    }
