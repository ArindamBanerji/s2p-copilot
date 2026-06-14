"""Read-only supplier lead-time intelligence for S2P fixtures."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from math import ceil
from statistics import mean, pstdev
from typing import Any

from pydantic import BaseModel


class LeadTimeStats(BaseModel):
    supplier_id: str
    supplier_name: str
    category: str
    season: str | None
    volume_band: str | None
    contractual_days: float
    actual_mean_days: float
    actual_std_days: float
    actual_p95_days: float
    sample_count: int
    on_time_pct: float
    trend: str
    alert: bool
    delta_vs_contract_days: float
    alert_level: str


class LeadTimeComputationResult(BaseModel):
    stats: list[LeadTimeStats]
    missing_timestamp_count: int
    skipped_negative_count: int
    skipped_missing_contract_count: int


def parse_date(value: Any) -> date | None:
    """Parse source date values without raising on invalid input."""
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).date()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def compute_actual_lead_time_days(invoice: dict[str, Any]) -> float | None:
    metadata = _metadata(invoice)
    po_date = parse_date(metadata.get("po_date"))
    gr_date = parse_date(metadata.get("gr_date"))
    if po_date is None or gr_date is None:
        return None
    delta = (gr_date - po_date).days
    if delta < 0:
        return None
    return float(delta)


def detect_trend(lead_times: list[float], window: int = 10) -> str:
    values = [float(value) for value in lead_times if value is not None]
    safe_window = max(int(window), 1)
    if len(values) <= safe_window:
        return "stable"
    recent = values[-safe_window:]
    historical = values[:-safe_window]
    if not historical:
        return "stable"
    recent_mean = mean(recent)
    historical_mean = mean(historical)
    historical_std = pstdev(historical) if len(historical) > 1 else 0.0
    threshold = historical_std if historical_std > 0 else 0.5
    if recent_mean > historical_mean + threshold:
        return "deteriorating"
    if recent_mean < historical_mean - threshold:
        return "improving"
    return "stable"


def compute_lead_times(
    invoices: list[dict[str, Any]],
    supplier_id: str | None = None,
    tolerance_days: float = 3.0,
    include_season: bool = True,
    include_volume_band: bool = True,
) -> list[LeadTimeStats]:
    return compute_lead_time_result(
        invoices,
        supplier_id=supplier_id,
        tolerance_days=tolerance_days,
        include_season=include_season,
        include_volume_band=include_volume_band,
    ).stats


def compute_lead_time_result(
    invoices: list[dict[str, Any]],
    supplier_id: str | None = None,
    tolerance_days: float = 3.0,
    include_season: bool = True,
    include_volume_band: bool = True,
) -> LeadTimeComputationResult:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    missing_timestamp_count = 0
    skipped_negative_count = 0
    skipped_missing_contract_count = 0

    for invoice in invoices:
        if not isinstance(invoice, dict):
            continue
        current_supplier = str(invoice.get("supplier_id") or "unknown")
        if supplier_id is not None and current_supplier != str(supplier_id):
            continue
        metadata = _metadata(invoice)
        po_date = parse_date(metadata.get("po_date"))
        gr_date = parse_date(metadata.get("gr_date"))
        if po_date is None or gr_date is None:
            missing_timestamp_count += 1
            continue
        actual = (gr_date - po_date).days
        if actual < 0:
            skipped_negative_count += 1
            continue
        contractual = _to_float(metadata.get("contractual_lead_time_days"))
        if contractual is None:
            skipped_missing_contract_count += 1
            continue
        category = str(invoice.get("category") or metadata.get("category") or "unknown")
        season = str(metadata.get("season") or "unknown") if include_season else ""
        volume_band = str(metadata.get("volume_band") or "unknown") if include_volume_band else ""
        key = (current_supplier, category, season, volume_band)
        groups[key].append(
            {
                "invoice": invoice,
                "actual": float(actual),
                "contractual": float(contractual),
            }
        )

    stats = [
        _stats_for_group(key, rows, tolerance_days)
        for key, rows in groups.items()
    ]
    stats.sort(
        key=lambda item: (
            item.supplier_id,
            item.category,
            item.season or "",
            item.volume_band or "",
        )
    )
    return LeadTimeComputationResult(
        stats=stats,
        missing_timestamp_count=missing_timestamp_count,
        skipped_negative_count=skipped_negative_count,
        skipped_missing_contract_count=skipped_missing_contract_count,
    )


def _stats_for_group(
    key: tuple[str, str, str, str],
    rows: list[dict[str, Any]],
    tolerance_days: float,
) -> LeadTimeStats:
    supplier_id, category, season, volume_band = key
    actuals = [float(row["actual"]) for row in rows]
    contractual_days = mean(float(row["contractual"]) for row in rows)
    actual_mean = mean(actuals)
    actual_std = pstdev(actuals) if len(actuals) > 1 else 0.0
    actual_p95 = _p95(actuals)
    on_time = sum(1 for value in actuals if value <= contractual_days + tolerance_days)
    delta = actual_mean - contractual_days
    alert = actual_mean > contractual_days + tolerance_days or actual_p95 > contractual_days + tolerance_days
    first_invoice = rows[0]["invoice"] if rows else {}
    return LeadTimeStats(
        supplier_id=supplier_id,
        supplier_name=str(first_invoice.get("supplier_name") or supplier_id),
        category=category,
        season=season or None,
        volume_band=volume_band or None,
        contractual_days=round(float(contractual_days), 2),
        actual_mean_days=round(float(actual_mean), 2),
        actual_std_days=round(float(actual_std), 2),
        actual_p95_days=round(float(actual_p95), 2),
        sample_count=len(actuals),
        on_time_pct=round(on_time / len(actuals), 4) if actuals else 0.0,
        trend=detect_trend(actuals),
        alert=alert,
        delta_vs_contract_days=round(float(delta), 2),
        alert_level=_alert_level(delta, actual_p95 - contractual_days, tolerance_days),
    )


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(ceil(0.95 * len(ordered)) - 1, 0)
    return ordered[min(index, len(ordered) - 1)]


def _alert_level(mean_delta: float, p95_delta: float, tolerance_days: float) -> str:
    excess = max(mean_delta, p95_delta)
    if excess <= tolerance_days:
        return "none"
    if excess <= tolerance_days + 2.0:
        return "watch"
    if excess <= tolerance_days + 5.0:
        return "elevated"
    return "critical"


def _metadata(invoice: dict[str, Any]) -> dict[str, Any]:
    metadata = invoice.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "LeadTimeComputationResult",
    "LeadTimeStats",
    "compute_actual_lead_time_days",
    "compute_lead_time_result",
    "compute_lead_times",
    "detect_trend",
    "parse_date",
]
