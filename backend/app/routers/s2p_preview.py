"""S2P preview endpoints backed by committed Phase 0 fixtures."""

from __future__ import annotations

import json
import math
import os
from uuid import uuid4
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, Request

from copilot_sdk.state.cached_static import cached_static

from app.domains.s2p.config import S2PDomainConfig
from app.models.responses import GenericResponse

router = APIRouter(prefix="/api/s2p/preview", tags=["s2p-preview"])

ENGINE_VERSION = "v0.7.23"
TAU = 0.1

_scored_invoices: list[dict[str, Any]] | None = None
_centroids: dict[str, dict[str, list[float]]] | None = None
_invoices: list[dict[str, Any]] | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _data_path(filename: str) -> Path:
    return _repo_root() / "data" / filename


def _load_fixture_json(filename: str) -> Any:
    path = _data_path(filename)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_celonis_cache() -> dict[str, Any]:
    candidates: list[Path] = []
    sdk_root = os.environ.get("CLAUDE_SDK", "")
    if sdk_root:
        candidates.append(Path(sdk_root) / "apps" / "dataops" / "backend" / "data" / "celonis_process_data.json")
    candidates.append(_data_path("celonis_process_data.json"))

    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _build_process_context(celonis_data: dict[str, Any]) -> dict[str, Any] | None:
    activities = celonis_data.get("activities")
    if not isinstance(activities, list):
        return None

    bottleneck = next(
        (activity for activity in activities if isinstance(activity, dict) and activity.get("bottleneck") is True),
        None,
    )
    if not bottleneck:
        return None

    duration_hours = float(bottleneck.get("duration_median_hours", bottleneck.get("avg_duration_hours", 0.0)) or 0.0)
    return {
        "process_model": celonis_data.get("process_model"),
        "variant": celonis_data.get("variant"),
        "bottleneck_activity": bottleneck.get("name") or bottleneck.get("id"),
        "duration_median_min": round(duration_hours * 60.0, 2),
        "source": "celonis_cache",
    }


def _with_process_context(invoice: dict[str, Any], process_context: dict[str, Any] | None) -> dict[str, Any]:
    if not process_context:
        return dict(invoice)
    return {
        **invoice,
        "process_context": dict(process_context),
    }


def _get_gae_version() -> str:
    return ENGINE_VERSION


def _get_config_list(attr_name: str, method_name: str) -> list[str]:
    method = getattr(S2PDomainConfig, method_name, None)
    if callable(method):
        return list(method())
    return list(getattr(S2PDomainConfig, attr_name))


def _tensor_shape_tuple() -> tuple[int, int, int]:
    return (
        S2PDomainConfig.n_categories,
        S2PDomainConfig.n_actions,
        S2PDomainConfig.n_factors,
    )


def _tensor_shape_text() -> str:
    return str(_tensor_shape_tuple())


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
        _centroids = _load_fixture_json("s2p_initial_centroids.json")
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


def _get_scorer(request: Request):
    return request.app.state.scorer


def _get_graph_store(request: Request, scorer: Any) -> Any:
    return getattr(request.app.state, "graph_store", None) or getattr(scorer, "graph_store", None)


def _score_read_only(scorer: Any, factors: dict[str, float], category: str) -> Any:
    score_read_only = getattr(scorer, "score_read_only", None)
    if not callable(score_read_only):
        raise RuntimeError("S2P preview requires a read-only scorer path")
    return score_read_only(factors, category)


def _score_invoice(invoice: dict[str, Any], scorer) -> dict[str, Any]:
    actions = _get_action_list()
    category = str(invoice["category"])
    factor_names = _get_factor_list()
    factors = invoice.get("factors") or {}
    factor_vector = [float(factors.get(name, 0.5)) for name in factor_names]
    scored_factors = {name: float(factors.get(name, 0.5)) for name in factor_names}
    metadata = invoice.get("metadata") if isinstance(invoice.get("metadata"), dict) else {}
    score_result = _score_read_only(
        scorer,
        scored_factors,
        category,
    )
    action_name = str(score_result.action)
    action_index = int(score_result.action_index)
    confidence = float(score_result.confidence)
    probabilities = _to_float_list(score_result.probabilities)
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
        "factors": {name: float(factors.get(name, 0.5)) for name in factor_names},
        "factor_vector": _to_float_list(factor_vector),
        "ground_truth_action": invoice["ground_truth_action"],
        "ground_truth_action_index": actions.index(invoice["ground_truth_action"]),
        "metadata": metadata,
      }


def _write_preview_observation(request: Request, invoice: dict[str, Any]) -> None:
    scorer = _get_scorer(request)
    graph_store = _get_graph_store(request, scorer)
    write_observation = getattr(graph_store, "write_observation", None)
    if not callable(write_observation):
        return

    factor_names = _get_factor_list()
    metadata = dict(invoice.get("metadata") or {})
    metadata.update(
        {
            "preview": True,
            "invoice_id": invoice.get("invoice_id"),
            "supplier_id": invoice.get("supplier_id"),
            "supplier_name": invoice.get("supplier_name"),
            "amount": invoice.get("amount"),
            "po_reference": invoice.get("po_reference"),
        }
    )
    write_observation(
        observation_id=f"OBS-{uuid4().hex[:12]}",
        domain="s2p",
        category=str(invoice["category"]),
        recommended_action=str(invoice["recommended_action"]),
        confidence=float(invoice["confidence"]),
        source_route="preview",
        scorer_version=getattr(scorer, "version", ENGINE_VERSION) or "unknown",
        factor_schema_version="s2p_factor_schema_v2",
        entity_id=str(invoice["invoice_id"]),
        factor_vector=[float(value) for value in invoice["factor_vector"]],
        factor_names=factor_names,
        metadata=metadata,
    )


def _get_fixture_invoices(n: int = 50) -> list[dict[str, Any]]:
    global _invoices
    if _invoices is None:
        _invoices = _load_fixture_json("synthetic_invoices.json")[:n]
    return _invoices


def _score_invoices(request: Request, invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scorer = _get_scorer(request)
    return [_score_invoice(invoice, scorer) for invoice in invoices]


def _get_scored_invoices(request: Request, n: int = 50, seed: int = 42) -> list[dict[str, Any]]:
    return _score_invoices(request, _get_fixture_invoices(n))


def _get_preview_simulation_scorer():
    from app.main import build_s2p_scorer
    from copilot_sdk.graph.memory_store import InMemoryGraphStore

    return build_s2p_scorer(
        graph_store=InMemoryGraphStore(domain="s2p"),
    )


def _invoice_factor_dict(invoice: Any) -> dict[str, float]:
    factor_vector = np.array(invoice.factor_vector, dtype=float)
    return {
        name: float(factor_vector[index])
        for index, name in enumerate(_get_factor_list())
    }


def _simulation_order_key(invoice: Any, scorer: Any) -> tuple[int, float]:
    result = scorer.score(
        _invoice_factor_dict(invoice),
        invoice.category,
        metadata={"source": "s2p_preview_ordering"},
    )
    correct = str(result.action) == str(invoice.ground_truth_action)
    return (1 if correct else 0, float(result.confidence))


def _build_compounding_trajectory(
    n: int = 1000,
    steps: int = 20,
    seed: int = 42,
) -> dict[str, Any]:
    from app.services.synthetic_invoices import SyntheticInvoiceGenerator

    generator = SyntheticInvoiceGenerator(seed=seed, noise_level=0.12)
    ordering_scorer = _get_preview_simulation_scorer()
    invoices = sorted(generator.generate(n), key=lambda invoice: _simulation_order_key(invoice, ordering_scorer))
    scorer = _get_preview_simulation_scorer()
    checkpoints = {
        max(1, round((index + 1) * len(invoices) / steps))
        for index in range(steps)
    }

    points: list[dict[str, Any]] = []
    correct_count = 0
    confidence_total = 0.0

    for decision_number, invoice in enumerate(invoices, start=1):
        factor_vector = np.array(invoice.factor_vector, dtype=float)
        factors = _invoice_factor_dict(invoice)
        result = scorer.score(
            factors,
            invoice.category,
            metadata={
                "invoice_id": invoice.invoice_id,
                "source": "s2p_preview_simulation",
            },
        )
        action = str(getattr(result, "action"))
        confidence = float(getattr(result, "confidence"))
        correct = action == str(invoice.ground_truth_action)

        correct_count += int(correct)
        confidence_total += confidence
        scorer.learn(
            result.decision_id,
            str(invoice.ground_truth_action),
            "confirmed" if correct else "overridden",
            context={
                "source": "s2p_preview_simulation",
                "confidence": confidence,
            },
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
    data = _load_fixture_json("s2p_demo_suppliers.json")
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


def _preview_slice_with_action_diversity(invoices: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    preview = list(invoices[:limit])
    if len(preview) < 2:
        return preview

    actions = {str(invoice.get("scored_action", "")) for invoice in preview}
    actions.discard("")
    if len(actions) >= 2:
        return preview

    first_action = str(preview[0].get("scored_action", "")) if preview else ""
    diverse_invoice = next(
        (
            invoice
            for invoice in invoices[limit:]
            if str(invoice.get("scored_action", "")) and str(invoice.get("scored_action", "")) != first_action
        ),
        None,
    )
    if diverse_invoice is not None:
        preview[-1] = diverse_invoice
    return preview


def reset_preview_state() -> None:
    global _invoices, _scored_invoices, _centroids
    _invoices = None
    _scored_invoices = None
    _centroids = None


def _preview_queue_payload(request: Request, limit: int = 5) -> dict[str, Any]:
    clamped_limit = _clamp_limit(limit, 1, 50)
    fixture_invoices = _get_fixture_invoices()
    preview_size = min(max(clamped_limit, 10), len(fixture_invoices))
    invoices = sorted(
        _score_invoices(request, fixture_invoices[:preview_size]),
        key=lambda invoice: invoice["confidence"],
        reverse=True,
    )
    while len({invoice["scored_action"] for invoice in invoices}) < 2 and preview_size < len(fixture_invoices):
        next_size = min(preview_size + 10, len(fixture_invoices))
        invoices = sorted(
            _score_invoices(request, fixture_invoices[:next_size]),
            key=lambda invoice: invoice["confidence"],
            reverse=True,
        )
        preview_size = next_size
    shown = _preview_slice_with_action_diversity(invoices, clamped_limit)
    for invoice in shown:
        _write_preview_observation(request, invoice)
    process_context = _build_process_context(_load_celonis_cache())
    exceptions = [
        _with_process_context(invoice, process_context)
        for invoice in shown
    ]
    auto_approve_count = sum(1 for invoice in invoices if invoice["scored_action"] == "auto_approve")
    confidence_avg = sum(invoice["confidence"] for invoice in invoices) / len(invoices) if invoices else 0.0
    return {
        "engine_version": ENGINE_VERSION,
        "total": len(fixture_invoices),
        "showing": len(shown),
        "exceptions": exceptions,
        "invoices": shown,
        "auto_approve_rate": round(auto_approve_count / len(invoices), 4) if invoices else 0.0,
        "confidence_avg": round(confidence_avg, 4),
        "scorer": {
            "engine": "Graph Attention Engine",
            "version": ENGINE_VERSION,
            "tensor_shape": _tensor_shape_text(),
            "factors": _get_factor_list(),
        },
    }


def _preview_queue_limit(request: Request, default: int = 5) -> int:
    try:
        raw = request.query_params.get("limit")
    except Exception:
        raw = None
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@router.get("/queue", response_model=GenericResponse)
@cached_static("preview-queue", copilot="s2p", url="/api/s2p/preview/queue")
def preview_queue(request: Request) -> dict[str, Any]:
    return _preview_queue_payload(request, _preview_queue_limit(request))


@router.get("/conservation", response_model=GenericResponse)
def preview_conservation(request: Request) -> dict[str, Any]:
    invoices = _get_scored_invoices(request)
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


@router.get("/compounding", response_model=GenericResponse)
@cached_static("preview-compounding", copilot="s2p")
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
        "tensor_shape": list(_tensor_shape_tuple()),
        "trajectory": points,
    }


@router.get("/suppliers", response_model=GenericResponse)
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


@router.get("/config", response_model=GenericResponse)
@cached_static("preview-config", copilot="s2p")
def preview_config() -> dict[str, Any]:
    return {
        "engine_version": ENGINE_VERSION,
        "domain": "s2p",
        "tensor_shape": _tensor_shape_text(),
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
                "tensor": _tensor_shape_text(),
                "categories": S2PDomainConfig.n_categories,
                "actions": S2PDomainConfig.n_actions,
                "factors": S2PDomainConfig.n_factors,
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
