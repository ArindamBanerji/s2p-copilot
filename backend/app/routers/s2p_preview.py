"""
S2P v2 preview endpoints backed by synthetic in-memory data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter

from app.domains.s2p.config import S2PDomainConfigV2
from app.services.synthetic_invoices import SyntheticInvoice, SyntheticInvoiceGenerator

router = APIRouter(prefix="/api/s2p/preview", tags=["s2p-preview"])

_scorer = None
_invoices: list[SyntheticInvoice] | None = None
_scored_invoices: list[dict[str, Any]] | None = None


def _get_gae_version() -> str:
    try:
        import gae

        return str(gae.__version__)
    except Exception:
        return "0.7.23"


def _get_config_list(attr_name: str, method_name: str) -> list[str]:
    method = getattr(S2PDomainConfigV2, method_name, None)
    if callable(method):
        return list(method())
    return list(getattr(S2PDomainConfigV2, attr_name))


def _get_category_list() -> list[str]:
    return _get_config_list("categories", "get_categories")


def _get_action_list() -> list[str]:
    return _get_config_list("actions", "get_actions")


def _get_factor_list() -> list[str]:
    return _get_config_list("factors", "get_factors")


def _get_canonical_factor_list() -> list[str]:
    return list(S2PDomainConfigV2.canonical_factors)


def _get_scorer():
    global _scorer
    if _scorer is None:
        from gae import ProfileScorer

        _scorer = ProfileScorer(
            mu=S2PDomainConfigV2.get_profile_centroids(),
            actions=_get_action_list(),
            profile=S2PDomainConfigV2.get_calibration_profile(),
            categories=_get_category_list(),
            eta_override=0.01,
        )
    return _scorer


def _to_float_list(values) -> list[float]:
    return [float(value) for value in values]


def _score_invoice(invoice: SyntheticInvoice) -> dict[str, Any]:
    scorer = _get_scorer()
    actions = _get_action_list()
    result = scorer.score(
        np.array(invoice.factor_vector, dtype=float),
        category_index=invoice.category_index,
    )
    action_index = int(getattr(result, "action_index"))
    action_name = getattr(result, "action_name", actions[action_index])

    return {
        "invoice_id": invoice.invoice_id,
        "supplier_id": invoice.supplier_id,
        "supplier_name": invoice.supplier_name,
        "category": invoice.category,
        "amount": float(invoice.amount),
        "po_reference": invoice.po_reference,
        "variance_pct": float(invoice.variance_pct),
        "recommended_action": str(action_name),
        "recommended_action_index": action_index,
        "confidence": float(getattr(result, "confidence")),
        "probabilities": _to_float_list(getattr(result, "probabilities")),
        "factors": {name: float(value) for name, value in invoice.factors.items()},
        "factor_vector": _to_float_list(invoice.factor_vector),
        "ground_truth_action": invoice.ground_truth_action,
        "ground_truth_action_index": int(invoice.ground_truth_action_index),
    }


def _get_scored_invoices(n: int = 50, seed: int = 42) -> list[dict[str, Any]]:
    global _invoices, _scored_invoices
    if _scored_invoices is None:
        generator = SyntheticInvoiceGenerator(seed=seed, noise_level=0.08)
        _invoices = generator.generate(n)
        _scored_invoices = [_score_invoice(invoice) for invoice in _invoices]
    return list(_scored_invoices)


def _get_supplier_fixture() -> list[dict[str, Any]]:
    fixture_path = Path(__file__).resolve().parent.parent / "data" / "s2p_demo_suppliers.json"
    if not fixture_path.exists():
        return []
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _clamp_limit(limit: int, minimum: int, maximum: int) -> int:
    if maximum <= 0:
        return 0
    return max(minimum, min(int(limit), maximum))


def reset_preview_state() -> None:
    global _scorer, _invoices, _scored_invoices
    _scorer = None
    _invoices = None
    _scored_invoices = None


@router.get("/queue")
def preview_queue(limit: int = 5) -> dict[str, Any]:
    invoices = sorted(
        _get_scored_invoices(),
        key=lambda invoice: invoice["amount"] * (1.0 - invoice["confidence"]),
        reverse=True,
    )
    clamped_limit = _clamp_limit(limit, 1, 50)
    shown = invoices[:clamped_limit]
    return {
        "total": len(invoices),
        "showing": len(shown),
        "invoices": shown,
        "scorer": {
            "engine": "Graph Attention Engine",
            "version": _get_gae_version(),
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
        "status": status,
        "auto_approve_pct": float(round(auto_approve_pct, 4)),
        "verified_decisions": int(len(invoices)),
        "copilot": "S2P Invoice Exception",
        "conservation_product": float(round(conservation_product, 6)),
        "conservation_threshold": 0.20,
        "engine_version": _get_gae_version(),
    }


@router.get("/compounding")
def preview_compounding() -> dict[str, Any]:
    points = []
    initial_accuracy = 0.72
    for batch in range(20):
        decisions = (batch + 1) * 50
        accuracy = initial_accuracy + (0.90 - initial_accuracy) * (batch / 19)
        points.append(
            {
                "decisions": int(decisions),
                "accuracy": float(round(accuracy, 4)),
                "batch": int(batch + 1),
            }
        )

    return {
        "initial_accuracy": initial_accuracy,
        "current_accuracy": float(points[-1]["accuracy"]),
        "total_decisions": 1000,
        "source": "synthetic_demo",
        "engine_version": _get_gae_version(),
        "trajectory": points,
    }


@router.get("/suppliers")
def preview_suppliers(limit: int = 2) -> dict[str, Any]:
    suppliers = _get_supplier_fixture()
    clamped_limit = _clamp_limit(limit, 1, len(suppliers))
    shown = suppliers[:clamped_limit]
    return {
        "total": len(suppliers),
        "showing": len(shown),
        "suppliers": shown,
        "source": "s2p_demo_suppliers.json",
    }


@router.get("/config")
def preview_config() -> dict[str, Any]:
    return {
        "domain": "s2p",
        "tensor_shape": "(5, 5, 7)",
        "categories": _get_category_list(),
        "actions": _get_action_list(),
        "factors": _get_factor_list(),
        "canonical_factors": _get_canonical_factor_list(),
        "penalty_ratio": 5.0,
        "engine_version": _get_gae_version(),
    }
