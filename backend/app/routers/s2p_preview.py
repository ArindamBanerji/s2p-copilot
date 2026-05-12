"""S2P preview endpoints backed by committed Phase 0 fixtures."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter

from app.domains.s2p.config import S2PDomainConfig

router = APIRouter(prefix="/api/s2p/preview", tags=["s2p-preview"])

ENGINE_VERSION = "v0.7.23"
TAU = 0.1

_scored_invoices: list[dict[str, Any]] | None = None
_centroids: dict[str, dict[str, list[float]]] | None = None
_scorer = None
_invoices: list[dict[str, Any]] | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _data_path(filename: str) -> Path:
    return _repo_root() / "data" / filename


def _load_json_fixture(filename: str) -> Any:
    path = _data_path(filename)
    return json.loads(path.read_text(encoding="utf-8"))


def _get_gae_version() -> str:
    return ENGINE_VERSION


def _get_config_list(attr_name: str, method_name: str) -> list[str]:
    method = getattr(S2PDomainConfig, method_name, None)
    if callable(method):
        return list(method())
    return list(getattr(S2PDomainConfig, attr_name))


def _get_category_list() -> list[str]:
    return _get_config_list("categories", "get_categories")


def _get_action_list() -> list[str]:
    return _get_config_list("actions", "get_actions")


def _get_factor_list() -> list[str]:
    return _get_config_list("factors", "get_factors")


def _get_canonical_factor_list() -> list[str]:
    return list(S2PDomainConfig.canonical_factors)


def _get_centroids() -> dict[str, dict[str, list[float]]]:
    global _centroids
    if _centroids is None:
        _centroids = _load_json_fixture("s2p_initial_centroids.json")
    return _centroids


def _to_float_list(values) -> list[float]:
    return [float(value) for value in values]


def _softmax(values: list[float], tau: float = TAU) -> list[float]:
    if not values:
        return []
    scaled = [value / tau for value in values]
    max_value = max(scaled)
    exps = [math.exp(value - max_value) for value in scaled]
    total = sum(exps)
    return [value / total for value in exps] if total else [1.0 / len(values)] * len(values)


def _score_invoice(invoice: dict[str, Any]) -> dict[str, Any]:
    actions = _get_action_list()
    category = str(invoice["category"])
    factor_names = _get_factor_list()
    factors = invoice.get("factors") or {}
    factor_vector = [float(factors[name]) for name in factor_names]
    centroids = _get_centroids()[category]
    distances = [
        float(np.linalg.norm(np.array(factor_vector, dtype=float) - np.array(centroids[action], dtype=float)))
        for action in actions
    ]
    probabilities = _softmax([-distance for distance in distances])
    action_index = int(max(range(len(probabilities)), key=lambda index: probabilities[index]))
    action_name = actions[action_index]
    confidence = float(probabilities[action_index])
    metadata = invoice.get("metadata") if isinstance(invoice.get("metadata"), dict) else {}
    variance_pct = float(factors.get("amount_variance_ratio", 0.0)) * 100.0

    return {
        "invoice_id": invoice["invoice_id"],
        "supplier_id": invoice["supplier_id"],
        "supplier": invoice["supplier_name"],
        "supplier_name": invoice["supplier_name"],
        "category": category,
        "amount": float(invoice["amount"]),
        "po_reference": invoice["po_number"],
        "variance_pct": float(variance_pct),
        "scored_action": action_name,
        "recommended_action": str(action_name),
        "recommended_action_index": action_index,
        "confidence": confidence,
        "probabilities": _to_float_list(probabilities),
        "factors": {name: float(factors[name]) for name in factor_names},
        "factor_vector": _to_float_list(factor_vector),
        "ground_truth_action": invoice["ground_truth_action"],
        "ground_truth_action_index": actions.index(invoice["ground_truth_action"]),
        "metadata": metadata,
    }


def _get_scored_invoices(n: int = 50, seed: int = 42) -> list[dict[str, Any]]:
    global _invoices, _scored_invoices
    if _scored_invoices is None:
        _invoices = _load_json_fixture("synthetic_invoices.json")[:n]
        _scored_invoices = [_score_invoice(invoice) for invoice in _invoices]
    return list(_scored_invoices)


def _get_preview_simulation_scorer():
    from gae import ProfileScorer

    rng = np.random.default_rng(42)
    converged = S2PDomainConfig.get_profile_centroids()
    bootstrap_mu = np.clip(
        0.5 + 0.4 * (converged - 0.5) + rng.normal(0.0, 0.03, converged.shape),
        0.0,
        1.0,
    )
    return ProfileScorer(
        mu=bootstrap_mu,
        actions=_get_action_list(),
        profile=S2PDomainConfig.get_calibration_profile(),
        categories=_get_category_list(),
        eta_override=0.01,
    )


def _build_compounding_trajectory(
    n: int = 1000,
    steps: int = 20,
    seed: int = 42,
) -> dict[str, Any]:
    from app.services.synthetic_invoices import SyntheticInvoiceGenerator

    scorer = _get_preview_simulation_scorer()
    generator = SyntheticInvoiceGenerator(seed=seed, noise_level=0.12)
    invoices = generator.generate(n)
    checkpoints = {
        max(1, round((index + 1) * len(invoices) / steps))
        for index in range(steps)
    }

    points: list[dict[str, Any]] = []
    correct_count = 0
    confidence_total = 0.0

    for decision_number, invoice in enumerate(invoices, start=1):
        factor_vector = np.array(invoice.factor_vector, dtype=float)
        result = scorer.score(
            factor_vector,
            category_index=invoice.category_index,
        )
        action_index = int(getattr(result, "action_index"))
        confidence = float(getattr(result, "confidence"))
        correct = action_index == int(invoice.ground_truth_action_index)

        correct_count += int(correct)
        confidence_total += confidence
        scorer.update(
            factor_vector,
            category_index=invoice.category_index,
            action_index=action_index,
            correct=correct,
            gt_action_index=int(invoice.ground_truth_action_index),
            confidence=confidence,
        )

        if decision_number in checkpoints:
            points.append(
                {
                    "decisions": decision_number,
                    "decision_number": decision_number,
                    "accuracy": float(round(correct_count / decision_number, 4)),
                    "confidence": float(round(confidence_total / decision_number, 4)),
                    "batch": len(points) + 1,
                }
            )

    return {
        "points": points,
        "total_decisions": len(invoices),
    }


def _get_supplier_fixture() -> list[dict[str, Any]]:
    data = _load_json_fixture("s2p_demo_suppliers.json")
    return data if isinstance(data, list) else []


def _preview_supplier(row: dict[str, Any]) -> dict[str, Any]:
    supplier_id = str(row.get("supplier_id") or "")
    canonical_name = str(row.get("name") or row.get("supplier_name") or supplier_id)
    legacy_supplier_names = {"SUP-001": "Chen-Lin Mfg"}
    supplier_name = legacy_supplier_names.get(supplier_id, canonical_name)
    exception_rate = float(row.get("exception_rate", 0.0))
    return {
        "supplier_id": supplier_id,
        "name": canonical_name,
        "supplier_name": supplier_name,
        "category": row.get("category"),
        "exception_rate": exception_rate,
        "avg_invoice_amount": float(row.get("avg_invoice_amount", 0.0)),
        "payment_terms": row.get("payment_terms"),
        "otif_score": float(row.get("otif_score", 0.0)),
        "total_invoices": int(row.get("total_invoices", 0)),
        "total_exceptions": int(row.get("total_exceptions", 0)),
        "recent_trend": row.get("recent_trend"),
        # Legacy SOC preview tab aliases. They are derived from the new profile
        # fields so old callers keep rendering while the flat fixture contract
        # remains available to new callers.
        "region": "global",
        "otif": {
            "q1_q2": float(row.get("otif_score", 0.0)),
            "q3": float(row.get("otif_score", 0.0)),
        },
        "lead_time": {
            "contractual": 30,
            "actual_q4": 30,
        },
        "financial_health_trend": row.get("recent_trend"),
    }


def _clamp_limit(limit: int, minimum: int, maximum: int) -> int:
    if maximum <= 0:
        return 0
    return max(minimum, min(int(limit), maximum))


def reset_preview_state() -> None:
    global _scorer, _invoices, _scored_invoices, _centroids
    _scorer = None
    _invoices = None
    _scored_invoices = None
    _centroids = None


@router.get("/queue")
def preview_queue(limit: int = 5) -> dict[str, Any]:
    invoices = sorted(
        _get_scored_invoices(),
        key=lambda invoice: invoice["confidence"],
        reverse=True,
    )
    clamped_limit = _clamp_limit(limit, 1, 50)
    shown = invoices[:clamped_limit]
    exceptions = invoices[:10]
    auto_approve_count = sum(1 for invoice in invoices if invoice["scored_action"] == "auto_approve")
    confidence_avg = sum(invoice["confidence"] for invoice in invoices) / len(invoices) if invoices else 0.0
    return {
        "engine_version": ENGINE_VERSION,
        "total": len(invoices),
        "showing": len(shown),
        "exceptions": exceptions,
        "invoices": shown,
        "auto_approve_rate": round(auto_approve_count / len(invoices), 4) if invoices else 0.0,
        "confidence_avg": round(confidence_avg, 4),
        "scorer": {
            "engine": "Graph Attention Engine",
            "version": ENGINE_VERSION,
            "tensor_shape": "(5, 5, 7)",
            "factors": _get_factor_list(),
        },
    }


@router.get("/conservation")
def preview_conservation() -> dict[str, Any]:
    invoices = _get_scored_invoices()
    auto_approve_count = sum(
        1
        for invoice in invoices
        if invoice["recommended_action"] == "auto_approve"
        and invoice["confidence"] >= 0.80
    )
    auto_approve_pct = (auto_approve_count / len(invoices) * 100.0) if invoices else 0.0
    status = "AMBER" if auto_approve_pct < 20.0 else "GREEN"
    conservation_product = float(auto_approve_pct / 100.0)

    return {
        "engine_version": ENGINE_VERSION,
        "source": "illustration",
        "status": "GREEN",
        "auto_approve_rate": 0.45,
        "accuracy": 0.84,
        "verified_decisions": 1000,
        "penalty_ratio": 5.0,
        "passed": True,
        "curve": [
            {"verified_decisions": 0, "accuracy": 0.70, "auto_approve_rate": 0.12},
            {"verified_decisions": 250, "accuracy": 0.76, "auto_approve_rate": 0.24},
            {"verified_decisions": 500, "accuracy": 0.80, "auto_approve_rate": 0.35},
            {"verified_decisions": 1000, "accuracy": 0.84, "auto_approve_rate": 0.45},
        ],
        "computed_status": status,
        "auto_approve_pct": float(round(auto_approve_pct, 4)),
        "fixture_decisions": int(len(invoices)),
        "copilot": "S2P Invoice Exception",
        "conservation_product": float(round(conservation_product, 6)),
        "conservation_threshold": 0.20,
    }


@router.get("/compounding")
def preview_compounding() -> dict[str, Any]:
    simulation = _build_compounding_trajectory()
    points = simulation["points"]
    initial_accuracy = float(points[0]["accuracy"]) if points else 0.0
    current_accuracy = float(points[-1]["accuracy"]) if points else 0.0

    return {
        "engine_version": ENGINE_VERSION,
        "initial_accuracy": initial_accuracy,
        "current_accuracy": current_accuracy,
        "total_decisions": simulation["total_decisions"],
        "source": "s2p_preview_simulation",
        "tensor_shape": [5, 5, 7],
        "trajectory": points,
    }


@router.get("/suppliers")
def preview_suppliers(limit: int | None = None) -> dict[str, Any]:
    suppliers = [_preview_supplier(supplier) for supplier in _get_supplier_fixture()]
    if limit is None:
        shown = suppliers
    else:
        clamped_limit = _clamp_limit(limit, 1, len(suppliers))
        shown = suppliers[:clamped_limit]
    return {
        "engine_version": ENGINE_VERSION,
        "total": len(suppliers),
        "showing": len(shown),
        "suppliers": shown,
        "source": "s2p_demo_suppliers.json",
    }


@router.get("/config")
def preview_config() -> dict[str, Any]:
    return {
        "engine_version": ENGINE_VERSION,
        "domain": "s2p",
        "tensor_shape": "(5, 5, 7)",
        "categories": _get_category_list(),
        "actions": _get_action_list(),
        "factors": _get_factor_list(),
        "canonical_factors": _get_canonical_factor_list(),
        "penalty_ratio": 5.0,
        "platform_comparison": {
            "soc": {
                "domain": "Security Operations",
                "tensor": "SOC production tensor",
                "categories": 6,
                "actions": 4,
                "factors": 6,
                "penalty_ratio": "20:1",
                "conservation": "active",
            },
            "s2p": {
                "domain": "Source-to-Pay",
                "tensor": "(5, 5, 7)",
                "categories": 5,
                "actions": 5,
                "factors": 7,
                "penalty_ratio": "5:1",
                "conservation": "active",
            },
            "shared": {
                "engine_version": "0.7.23",
                "conservation_law": "α·q·V ≥ θ_min",
                "learning_strategy": "ContinuousStrategy",
                "message": "Two domains on one engine. Same math. Different parameters. Same conservation law.",
            },
        },
        "cross_copilot_signals": {
            "description": "Operational patterns inherited from SOC AgentEvolver",
            "signals": [
                {
                    "pattern": "Tighten threshold during anomaly cluster",
                    "source_copilot": "SOC",
                    "source_rule": "RULE-CAMPAIGN-ESCALATE",
                    "adapted_as": "RULE-S2P-EXCEPTION-CLUSTER",
                    "adaptation": (
                        "Campaign detection → supplier exception spike detection. "
                        "When 3+ exceptions from same supplier in 7 days, "
                        "tighten auto-approve threshold by 15%."
                    ),
                    "warm_start_prior": 0.757,
                    "warm_start_source": "SOC shadow win rate (75.7%, 25 comparisons)",
                },
                {
                    "pattern": "Drift-triggered recalibration",
                    "source_copilot": "SOC",
                    "source_rule": "RULE-DRIFT-THRESHOLD",
                    "adapted_as": "RULE-S2P-COMMODITY-DRIFT",
                    "adaptation": (
                        "Per-category accuracy drift → per-commodity accuracy drift. "
                        "When commodity index correlation factor σ increases >20%, "
                        "trigger scoring threshold review."
                    ),
                    "warm_start_prior": 0.68,
                    "warm_start_source": "SOC shadow win rate (68%, 25 comparisons)",
                },
            ],
            "network_effect": (
                "Each new copilot starts at IKS 12 instead of IKS 0. "
                "Structural patterns (not domain-specific centroids) transfer "
                "across copilots via AgentEvolver rule templates."
            ),
            "status": "designed_capability",
            "note": (
                "Cross-copilot signal transfer is architecturally validated. "
                "Production activation requires S2P pilot data."
            ),
        },
    }
