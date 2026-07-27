"""P28-backed S2P financial impact endpoints."""

from __future__ import annotations

from copy import deepcopy
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.services.financial_impact import FinancialSummary, compute_financial_impact
from app.services.receipt_store import get_receipt_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/s2p/financial-impact", tags=["s2p-financial-impact"])

_FINANCIAL_SNAPSHOT: dict[str, Any] | None = None
_FINANCIAL_TREND_SNAPSHOT: dict[str, Any] | None = None
_FINANCIAL_CATEGORY_SNAPSHOTS: dict[str, dict[str, Any]] = {}
_FINANCIAL_SNAPSHOT_GRAPH_STORE: Any | None = None


class FinancialImpactSummaryResponse(BaseModel):
    total_decisions: int = 0
    verified_decisions: int = 0
    total_amount: float = 0.0
    total_at_risk: float = 0.0
    total_recovered: float = 0.0
    net_savings: float = 0.0
    recovery_rate: float = 0.0
    missing_receipts: int = 0
    by_supplier: dict[str, dict[str, float | int]] = Field(default_factory=dict)
    by_category: dict[str, dict[str, float | int]] = Field(default_factory=dict)


class FinancialImpactCategoryResponse(FinancialImpactSummaryResponse):
    category: str
    allowed_categories: list[str]


class FinancialImpactTrendPoint(BaseModel):
    week: str
    start_date: str | None = None
    end_date: str | None = None
    total_decisions: int = 0
    verified_decisions: int = 0
    total_amount: float = 0.0
    total_at_risk: float = 0.0
    total_recovered: float = 0.0
    net_savings: float = 0.0
    recovery_rate: float = 0.0
    missing_receipts: int = 0


class FinancialImpactTrendResponse(BaseModel):
    window_weeks: int
    as_of: str | None = None
    points: list[FinancialImpactTrendPoint] = Field(default_factory=list)
    totals: FinancialImpactSummaryResponse = Field(default_factory=FinancialImpactSummaryResponse)


def _summary_response(summary: FinancialSummary) -> dict[str, Any]:
    return summary.to_dict()


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _graph_store(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is not None:
        return graph_store
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _graph_domain(graph_store: Any | None) -> str:
    return str(getattr(graph_store, "domain", None) or "s2p")


def _graph_reader(request: Request) -> S2PGraphReader:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    graph_store = getattr(scorer, "graph_store", None)
    reader = getattr(state, "s2p_graph_reader", None)
    if isinstance(reader, S2PGraphReader) and reader.store is graph_store:
        return reader
    if graph_store is None:
        raise HTTPException(status_code=503, detail="S2P graph reader unavailable")
    return S2PGraphReader(store=graph_store)


def _all_graph_decisions(reader: S2PGraphReader) -> list[dict[str, Any]]:
    rows = reader.get_all_decisions()
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes, dict)):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _receipt_rows(limit: int = 10000) -> list[dict[str, Any]]:
    store = get_receipt_store()
    try:
        rows = store.get_chain(limit=limit)
    except TypeError:
        rows = store.get_chain()
    except Exception as exc:
        log.exception("Failed to read S2P financial receipts")
        raise HTTPException(
            status_code=500,
            detail="Unable to read financial impact receipts",
        ) from exc
    return [dict(row) for row in rows if isinstance(row, dict)]


def _record_category(record: Any) -> str | None:
    category = _get_value(record, "category")
    return str(category) if category is not None else None


def _record_key(record: Any) -> str | None:
    value = _get_value(record, "decision_id") or _get_value(record, "invoice_id")
    return str(value) if value is not None else None


def _record_keys(record: Any) -> set[str]:
    keys: set[str] = set()
    for field_name in ("decision_id", "invoice_id"):
        value = _get_value(record, field_name)
        if value is not None:
            keys.add(str(value))
    return keys


def _receipts_by_identity(receipts: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    lookup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for receipt in receipts:
        for key in _record_keys(receipt):
            lookup[key].append(receipt)
    return lookup


def _matched_receipts_for_decisions(
    decisions: list[dict[str, Any]],
    receipt_lookup: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    seen: set[int] = set()
    for decision in decisions:
        for key in _record_keys(decision):
            for receipt in receipt_lookup.get(key, []):
                marker = id(receipt)
                if marker not in seen:
                    seen.add(marker)
                    matched.append(receipt)
    return matched


def _filter_category(
    decisions: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    category: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    filtered_decisions = [
        decision for decision in decisions if _record_category(decision) == category
    ]
    decision_keys = {
        key for decision in filtered_decisions if (key := _record_key(decision)) is not None
    }
    filtered_receipts = [
        receipt
        for receipt in receipts
        if _record_category(receipt) == category
        or (_record_key(receipt) is not None and _record_key(receipt) in decision_keys)
    ]
    return filtered_decisions, filtered_receipts


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.fromtimestamp(float(text), timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _record_timestamp(record: Any) -> datetime | None:
    for field_name in ("created_at", "timestamp", "verified_at", "updated_at"):
        if timestamp := _parse_timestamp(_get_value(record, field_name)):
            return timestamp
    return None


def _week_key(dt: datetime) -> tuple[int, int]:
    iso = dt.isocalendar()
    return int(iso.year), int(iso.week)


def _week_start(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _trend_response(
    decisions: list[dict[str, Any]],
    receipts: list[dict[str, Any]],
    window_weeks: int = 12,
    max_decisions: int | None = 500,
) -> dict[str, Any]:
    timestamps = [
        timestamp
        for record in [*decisions, *receipts]
        if (timestamp := _record_timestamp(record)) is not None
    ]
    if not timestamps:
        return {
            "window_weeks": window_weeks,
            "as_of": None,
            "points": [],
            "totals": FinancialImpactSummaryResponse().model_dump(),
        }

    as_of = max(timestamps)
    min_week_start = _week_start(as_of) - timedelta(weeks=max(window_weeks - 1, 0))
    decisions_by_week: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    receipt_lookup = _receipts_by_identity(receipts)

    for decision in decisions:
        timestamp = _record_timestamp(decision)
        if timestamp is not None and timestamp >= min_week_start:
            decisions_by_week[_week_key(timestamp)].append(decision)

    points: list[dict[str, Any]] = []
    for week in sorted(decisions_by_week):
        year, week_number = week
        week_start = datetime.fromisocalendar(year, week_number, 1).replace(tzinfo=timezone.utc)
        week_end = week_start + timedelta(days=6)
        week_decisions = decisions_by_week[week]
        week_receipts = _matched_receipts_for_decisions(week_decisions, receipt_lookup)
        summary = compute_financial_impact(
            week_decisions,
            week_receipts,
            max_decisions=max_decisions,
        )
        point = {
            **_summary_response(summary),
            "week": f"{year}-W{week_number:02d}",
            "start_date": week_start.date().isoformat(),
            "end_date": week_end.date().isoformat(),
        }
        point.pop("by_supplier", None)
        point.pop("by_category", None)
        points.append(point)

    window_decisions = [item for rows in decisions_by_week.values() for item in rows]
    totals = compute_financial_impact(
        window_decisions,
        _matched_receipts_for_decisions(window_decisions, receipt_lookup),
        max_decisions=max_decisions,
    )
    return {
        "window_weeks": window_weeks,
        "as_of": as_of.isoformat(),
        "points": points[-window_weeks:],
        "totals": _summary_response(totals),
    }


def warm_financial_snapshots(
    graph_store: Any | None,
    max_decisions: int | None = None,
    reader: S2PGraphReader | None = None,
) -> None:
    """Materialize financial responses after seeded state is available."""
    global _FINANCIAL_SNAPSHOT
    global _FINANCIAL_TREND_SNAPSHOT
    global _FINANCIAL_CATEGORY_SNAPSHOTS
    global _FINANCIAL_SNAPSHOT_GRAPH_STORE

    decisions = _all_graph_decisions(reader or S2PGraphReader(store=graph_store))
    receipts = _receipt_rows()
    allowed_categories = list(S2PDomainConfig.categories)
    _FINANCIAL_SNAPSHOT = _summary_response(
        compute_financial_impact(decisions, receipts, max_decisions=max_decisions)
    )
    _FINANCIAL_TREND_SNAPSHOT = _trend_response(
        decisions,
        receipts,
        window_weeks=12,
        max_decisions=max_decisions,
    )
    _FINANCIAL_CATEGORY_SNAPSHOTS = {}
    for category in allowed_categories:
        filtered_decisions, filtered_receipts = _filter_category(decisions, receipts, category)
        _FINANCIAL_CATEGORY_SNAPSHOTS[category] = {
            **_summary_response(
                compute_financial_impact(
                    filtered_decisions,
                    filtered_receipts,
                    max_decisions=max_decisions,
                )
            ),
            "category": category,
            "allowed_categories": allowed_categories,
        }
    _FINANCIAL_SNAPSHOT_GRAPH_STORE = graph_store


def reset_financial_snapshots() -> None:
    """Clear snapshots for an explicit demo reset or isolated test."""
    global _FINANCIAL_SNAPSHOT
    global _FINANCIAL_TREND_SNAPSHOT
    global _FINANCIAL_CATEGORY_SNAPSHOTS
    global _FINANCIAL_SNAPSHOT_GRAPH_STORE

    _FINANCIAL_SNAPSHOT = None
    _FINANCIAL_TREND_SNAPSHOT = None
    _FINANCIAL_CATEGORY_SNAPSHOTS = {}
    _FINANCIAL_SNAPSHOT_GRAPH_STORE = None


def _ensure_financial_snapshots(request: Request) -> None:
    graph_store = _graph_store(request)
    if _FINANCIAL_SNAPSHOT is None or _FINANCIAL_SNAPSHOT_GRAPH_STORE is not graph_store:
        try:
            warm_financial_snapshots(
                graph_store,
                max_decisions=500,
                reader=_graph_reader(request),
            )
        except GraphUnavailableError as exc:
            log.exception("Failed to read S2P financial decisions")
            raise HTTPException(
                status_code=503,
                detail="S2P graph unavailable for financial impact",
            ) from exc


@router.get("", response_model=FinancialImpactSummaryResponse)
def financial_impact(request: Request) -> dict[str, Any]:
    _ensure_financial_snapshots(request)
    return deepcopy(_FINANCIAL_SNAPSHOT or {})


@router.get("/trend", response_model=FinancialImpactTrendResponse)
def financial_impact_trend(request: Request) -> dict[str, Any]:
    _ensure_financial_snapshots(request)
    return deepcopy(_FINANCIAL_TREND_SNAPSHOT or {})


@router.get("/{category}", response_model=FinancialImpactCategoryResponse)
def financial_impact_category(category: str, request: Request) -> dict[str, Any]:
    allowed_categories = list(S2PDomainConfig.categories)
    if category not in allowed_categories:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Unsupported S2P financial impact category: {category}",
                "allowed_categories": allowed_categories,
            },
        )
    _ensure_financial_snapshots(request)
    return deepcopy(_FINANCIAL_CATEGORY_SNAPSHOTS[category])
