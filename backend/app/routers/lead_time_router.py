"""Read-only S2P supplier lead-time intelligence endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.routers.s2p_data_helpers import load_invoices, load_suppliers
from app.services.lead_time import LeadTimeStats, compute_lead_time_result


router = APIRouter(prefix="/api/s2p/lead-time", tags=["s2p-lead-time"])


class LeadTimeSummaryResponse(BaseModel):
    stats: list[LeadTimeStats]
    total_groups: int
    total_samples: int
    supplier_count: int
    category_count: int
    alert_count: int
    missing_timestamp_count: int
    skipped_negative_count: int
    skipped_missing_contract_count: int
    tolerance_days: float


class SupplierLeadTimeSummary(BaseModel):
    supplier_id: str
    supplier_name: str
    total_groups: int
    total_samples: int
    alert_count: int
    actual_mean_days: float
    actual_p95_days: float
    contractual_days: float
    alert_level: str


class LeadTimeSuppliersResponse(BaseModel):
    suppliers: list[SupplierLeadTimeSummary]
    total_suppliers: int


class LeadTimeSupplierResponse(BaseModel):
    supplier_id: str
    supplier_name: str
    stats: list[LeadTimeStats]
    total_groups: int
    total_samples: int
    warnings: list[str]


class LeadTimeAlertsResponse(BaseModel):
    alerts: list[LeadTimeStats]
    total_alerts: int
    tolerance_days: float


@router.get("/summary", response_model=LeadTimeSummaryResponse)
def lead_time_summary(
    supplier_id: str | None = None,
    tolerance_days: float = Query(3.0, ge=0.0),
) -> LeadTimeSummaryResponse:
    result = compute_lead_time_result(
        load_invoices(),
        supplier_id=supplier_id,
        tolerance_days=tolerance_days,
    )
    return _summary_response(result, tolerance_days)


@router.get("/suppliers", response_model=LeadTimeSuppliersResponse)
def lead_time_suppliers(
    tolerance_days: float = Query(3.0, ge=0.0),
) -> LeadTimeSuppliersResponse:
    result = compute_lead_time_result(load_invoices(), tolerance_days=tolerance_days)
    by_supplier: dict[str, list[LeadTimeStats]] = {}
    for stat in result.stats:
        by_supplier.setdefault(stat.supplier_id, []).append(stat)
    summaries = [
        _supplier_summary(supplier_id, stats)
        for supplier_id, stats in sorted(by_supplier.items())
    ]
    return LeadTimeSuppliersResponse(suppliers=summaries, total_suppliers=len(summaries))


@router.get("/alerts", response_model=LeadTimeAlertsResponse)
def lead_time_alerts(
    tolerance_days: float = Query(3.0, ge=0.0),
) -> LeadTimeAlertsResponse:
    result = compute_lead_time_result(load_invoices(), tolerance_days=tolerance_days)
    alerts = [stat for stat in result.stats if stat.alert]
    return LeadTimeAlertsResponse(
        alerts=alerts,
        total_alerts=len(alerts),
        tolerance_days=float(tolerance_days),
    )


@router.get("/suppliers/{supplier_id}", response_model=LeadTimeSupplierResponse)
def lead_time_supplier(
    supplier_id: str,
    tolerance_days: float = Query(3.0, ge=0.0),
) -> LeadTimeSupplierResponse:
    supplier_name = _supplier_name(supplier_id)
    invoice_supplier_ids = {str(invoice.get("supplier_id") or "") for invoice in load_invoices()}
    known_supplier_ids = {str(supplier.get("supplier_id") or "") for supplier in load_suppliers()}
    if supplier_id not in invoice_supplier_ids and supplier_id not in known_supplier_ids:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")

    result = compute_lead_time_result(
        load_invoices(),
        supplier_id=supplier_id,
        tolerance_days=tolerance_days,
    )
    warnings = []
    if not result.stats:
        warnings.append("No valid lead-time samples found for supplier")
    return LeadTimeSupplierResponse(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        stats=result.stats,
        total_groups=len(result.stats),
        total_samples=sum(stat.sample_count for stat in result.stats),
        warnings=warnings,
    )


def _summary_response(result: Any, tolerance_days: float) -> LeadTimeSummaryResponse:
    return LeadTimeSummaryResponse(
        stats=result.stats,
        total_groups=len(result.stats),
        total_samples=sum(stat.sample_count for stat in result.stats),
        supplier_count=len({stat.supplier_id for stat in result.stats}),
        category_count=len({stat.category for stat in result.stats}),
        alert_count=sum(1 for stat in result.stats if stat.alert),
        missing_timestamp_count=result.missing_timestamp_count,
        skipped_negative_count=result.skipped_negative_count,
        skipped_missing_contract_count=result.skipped_missing_contract_count,
        tolerance_days=float(tolerance_days),
    )


def _supplier_summary(supplier_id: str, stats: list[LeadTimeStats]) -> SupplierLeadTimeSummary:
    sample_count = sum(stat.sample_count for stat in stats)
    if sample_count:
        actual_mean = sum(stat.actual_mean_days * stat.sample_count for stat in stats) / sample_count
        contractual = sum(stat.contractual_days * stat.sample_count for stat in stats) / sample_count
    else:
        actual_mean = 0.0
        contractual = 0.0
    return SupplierLeadTimeSummary(
        supplier_id=supplier_id,
        supplier_name=stats[0].supplier_name if stats else _supplier_name(supplier_id),
        total_groups=len(stats),
        total_samples=sample_count,
        alert_count=sum(1 for stat in stats if stat.alert),
        actual_mean_days=round(actual_mean, 2),
        actual_p95_days=max((stat.actual_p95_days for stat in stats), default=0.0),
        contractual_days=round(contractual, 2),
        alert_level=_max_alert_level(stat.alert_level for stat in stats),
    )


def _max_alert_level(levels: Any) -> str:
    order = {"none": 0, "watch": 1, "elevated": 2, "critical": 3}
    best = "none"
    best_score = 0
    for level in levels:
        score = order.get(str(level), 0)
        if score > best_score:
            best = str(level)
            best_score = score
    return best


def _supplier_name(supplier_id: str) -> str:
    for supplier in load_suppliers():
        if str(supplier.get("supplier_id") or "") == supplier_id:
            return str(supplier.get("name") or supplier.get("supplier_name") or supplier_id)
    for invoice in load_invoices():
        if str(invoice.get("supplier_id") or "") == supplier_id:
            return str(invoice.get("supplier_name") or supplier_id)
    return supplier_id


__all__ = [
    "LeadTimeAlertsResponse",
    "LeadTimeSummaryResponse",
    "LeadTimeSupplierResponse",
    "LeadTimeSuppliersResponse",
    "router",
]
