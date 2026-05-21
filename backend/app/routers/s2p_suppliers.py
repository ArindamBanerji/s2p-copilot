"""S2P supplier profile, clustering, and heatmap endpoints."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors
from app.routers.s2p_data_helpers import load_invoices, load_suppliers
from app.services.supplier_profile_accumulator import SupplierProfile, accumulator

router = APIRouter(prefix="/api/s2p/suppliers", tags=["s2p-suppliers"])


def _supplier_invoices(supplier_id: str) -> list[dict[str, Any]]:
    return [invoice for invoice in load_invoices() if invoice.get("supplier_id") == supplier_id]


def _find_supplier(supplier_id: str) -> dict[str, Any]:
    for supplier in load_suppliers():
        if supplier.get("supplier_id") == supplier_id:
            return supplier
    raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")


def _stable_series(supplier_id: str, base: float, points: int = 6) -> list[float]:
    digest = hashlib.sha256(supplier_id.encode("utf-8")).digest()
    values = []
    for index in range(points):
        offset = ((digest[index] / 255.0) - 0.5) * 0.08
        values.append(round(max(0.0, min(1.0, base + offset)), 4))
    return values


def _risk_level(exception_rate: float, otif_score: float) -> str:
    if exception_rate >= 0.12 or otif_score < 0.86:
        return "red"
    if exception_rate >= 0.07 or otif_score < 0.92:
        return "amber"
    return "green"


def _category_distribution(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(str(invoice.get("category") or "uncategorized") for invoice in invoices)
    return [
        {"category": category, "count": count}
        for category, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _summary(supplier: dict[str, Any]) -> dict[str, Any]:
    supplier_id = str(supplier.get("supplier_id") or "")
    invoices = _supplier_invoices(supplier_id)
    return {
        "supplier_id": supplier_id,
        "name": supplier.get("name") or supplier.get("supplier_name") or supplier_id,
        "category": supplier.get("category"),
        "otif_score": float(supplier.get("otif_score") or 0.0),
        "exception_rate": float(supplier.get("exception_rate") or 0.0),
        "invoice_count": len(invoices),
        "category_distribution": _category_distribution(invoices),
        "trend_direction": supplier.get("recent_trend") or "stable",
    }


def _trend_direction(exception_rate_trend: float | None) -> str:
    if exception_rate_trend is None:
        return "stable"
    if exception_rate_trend > 0.0:
        return "declining"
    if exception_rate_trend < 0.0:
        return "improving"
    return "stable"


def _profile_summary(profile: SupplierProfile) -> dict[str, Any]:
    supplier_id = str(profile.supplier_id)
    invoices = _supplier_invoices(supplier_id)
    otif_score = float(profile.otif or 0.0)
    data = asdict(profile)
    data.update(
        {
            "supplier_id": supplier_id,
            "name": profile.supplier_name,
            "supplier_name": profile.supplier_name,
            "category": profile.categories[0] if profile.categories else None,
            "otif_score": otif_score,
            "exception_rate": float(profile.exception_rate or 0.0),
            "invoice_count": int(profile.invoice_count or 0),
            "category_distribution": _category_distribution(invoices),
            "trend_direction": _trend_direction(profile.exception_rate_trend),
        }
    )
    return data


def _profile_detail(profile: SupplierProfile) -> dict[str, Any]:
    summary = _profile_summary(profile)
    supplier_id = str(profile.supplier_id)
    invoices = _supplier_invoices(supplier_id)
    exception_rate = float(summary["exception_rate"])
    otif_score = float(summary["otif_score"])
    return {
        **summary,
        "otif_trend": _stable_series(supplier_id, otif_score),
        "exception_trend": _stable_series(f"{supplier_id}:exception", exception_rate),
        "top_categories": _category_distribution(invoices)[:5],
        "recent_invoices": [
            {
                "invoice_id": invoice.get("invoice_id"),
                "amount": float(invoice.get("amount") or 0.0),
                "category": invoice.get("category"),
                "ground_truth_action": invoice.get("ground_truth_action"),
            }
            for invoice in invoices[:5]
        ],
        "behavioral_cluster": _cluster_label(_cluster_name(summary)),
        "risk_level": _risk_level(exception_rate, otif_score),
    }


def _cluster_name(summary: dict[str, Any]) -> str:
    if summary["otif_score"] >= 0.94 and summary["exception_rate"] <= 0.05:
        return "high_reliability"
    if summary["invoice_count"] >= 5:
        return "volume_leaders"
    if summary["exception_rate"] >= 0.10 or summary["trend_direction"] == "declining":
        return "risk_watch"
    return "new_low_volume"


def _cluster_label(cluster_id: str) -> str:
    return {
        "high_reliability": "High Reliability",
        "volume_leaders": "Volume Leaders",
        "risk_watch": "Risk Watch",
        "new_low_volume": "New/Low Volume",
    }[cluster_id]


def _cluster_description(cluster_id: str) -> str:
    return {
        "high_reliability": "Strong OTIF performance and low exception rates.",
        "volume_leaders": "Suppliers with the most fixture invoice activity.",
        "risk_watch": "Exception-heavy or declining suppliers requiring monitoring.",
        "new_low_volume": "Low-volume suppliers with limited recent invoice evidence.",
    }[cluster_id]


@router.get("")
@router.get("/")
def suppliers() -> dict[str, Any]:
    rows = [_profile_summary(profile) for profile in accumulator.get_all_profiles()]
    return {
        "suppliers": rows,
        "total": len(rows),
        "source": "accumulator_with_fixture_fallback",
    }


@router.get("/clustering")
def clustering() -> dict[str, Any]:
    summaries = [_summary(supplier) for supplier in load_suppliers()]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in summaries:
        buckets[_cluster_name(summary)].append(summary)

    clusters = []
    for cluster_id in ("high_reliability", "volume_leaders", "risk_watch", "new_low_volume"):
        members = buckets.get(cluster_id, [])
        avg_otif = sum(member["otif_score"] for member in members) / len(members) if members else 0.0
        avg_exception_rate = (
            sum(member["exception_rate"] for member in members) / len(members)
            if members
            else 0.0
        )
        clusters.append(
            {
                "cluster_id": cluster_id,
                "cluster_name": _cluster_label(cluster_id),
                "supplier_ids": [member["supplier_id"] for member in members],
                "avg_otif": round(avg_otif, 4),
                "avg_exception_rate": round(avg_exception_rate, 4),
                "description": _cluster_description(cluster_id),
            }
        )
    return {"clusters": clusters, "total_clusters": len(clusters), "method": "threshold"}


@router.get("/declining")
def declining_suppliers() -> dict[str, Any]:
    rows = [_profile_summary(profile) for profile in accumulator.get_declining_suppliers()]
    return {"suppliers": rows, "total": len(rows), "source": "accumulator"}


@router.get("/{supplier_id}/profile")
def profile(supplier_id: str) -> dict[str, Any]:
    supplier_profile = accumulator.get_profile(supplier_id)
    if supplier_profile is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    return _profile_detail(supplier_profile)


@router.get("/{supplier_id}/history")
def supplier_history(supplier_id: str, limit: int = 200) -> dict[str, Any]:
    supplier_profile = accumulator.get_profile(supplier_id)
    if supplier_profile is None:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    events = accumulator.get_supplier_history(supplier_id, limit)
    return {"events": events, "total": len(events)}


@router.get("/{supplier_id}/heatmap")
def heatmap(supplier_id: str) -> dict[str, Any]:
    _find_supplier(supplier_id)
    invoices = _supplier_invoices(supplier_id)
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for invoice in invoices:
        by_category[str(invoice.get("category") or "uncategorized")].append(invoice)

    categories = []
    for category in S2PDomainConfig.categories:
        category_invoices = by_category.get(category, [])
        exception_count = len(category_invoices)
        categories.append(
            {
                "category": category,
                "invoice_count": len(category_invoices),
                "exception_count": exception_count,
                "exception_rate": round(exception_count / len(category_invoices), 4)
                if category_invoices
                else 0.0,
            }
        )

    factor_totals = {factor: 0.0 for factor in S2PDomainConfig.factors}
    for invoice in invoices:
        factors = compute_all_factors(invoice)
        for factor in S2PDomainConfig.factors:
            factor_totals[factor] += float(factors.get(factor, 0.0))
    factor_count = max(len(invoices), 1)
    factors = [
        {"factor": factor, "value": round(total / factor_count, 4)}
        for factor, total in factor_totals.items()
    ]
    return {
        "supplier_id": supplier_id,
        "categories": categories,
        "factors": factors,
        "invoice_count": len(invoices),
    }
