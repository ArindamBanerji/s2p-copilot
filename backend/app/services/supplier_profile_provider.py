"""Shared graph-backed supplier profiles with labeled fixture fallback."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_DEFAULT_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "s2p_demo_suppliers.json"
)
_UNAVAILABLE_FIELDS = (
    "region",
    "financial_health_trend",
    "pricing_trend",
    "payment_response",
)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _normalized(row: dict[str, Any]) -> dict[str, Any]:
    outcome = _mapping(row.get("outcome_metadata"))
    return {
        **_mapping(row.get("metadata")),
        **row,
        **outcome,
        **_mapping(outcome.get("context")),
        **_mapping(row.get("context")),
    }


def _verified(row: dict[str, Any]) -> bool:
    return (
        row.get("status") in {"confirmed", "overridden", "verified"}
        or row.get("correct") is not None
        or row.get("is_correct") is not None
        or row.get("verified") is True
        or bool(row.get("actual_action"))
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(row: dict[str, Any]) -> float:
    value = (
        row.get("created_at")
        or row.get("timestamp_epoch")
        or row.get("timestamp")
        or row.get("invoice_date")
        or 0
    )
    number = _number(value)
    if number is not None:
        return number
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()


def _mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _supplier_id(row: dict[str, Any]) -> str:
    return str(
        row.get("supplier_id")
        or row.get("supplier_entity_id")
        or row.get("vendor_id")
        or ""
    ).strip()


def _invoice_id(row: dict[str, Any]) -> str:
    return str(
        row.get("invoice_id")
        or row.get("source_invoice_id")
        or row.get("entity_id")
        or row.get("decision_id")
        or ""
    ).strip()


def _otif(row: dict[str, Any]) -> float | None:
    direct = _number(row.get("otif_score"))
    if direct is not None:
        return direct
    if row.get("on_time") is not None and row.get("in_full") is not None:
        return float(bool(row["on_time"]) and bool(row["in_full"]))
    return None


def _fixture_rows(fixture_path: str | Path | None) -> list[dict[str, Any]]:
    path = Path(fixture_path) if fixture_path is not None else _DEFAULT_FIXTURE_PATH
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Supplier fixture must be a list of supplier objects")
    return [dict(row) for row in rows]


def fixture_supplier_ids(fixture_path: str | Path | None = None) -> list[str]:
    """Return canonical preview IDs in fixture order."""
    return [supplier_id for row in _fixture_rows(fixture_path) if (supplier_id := _supplier_id(row))]


def _quarter_key(row: dict[str, Any]) -> str | None:
    timestamp = _timestamp(row)
    if timestamp <= 0:
        return None
    moment = datetime.fromtimestamp(timestamp, timezone.utc)
    return f"Q{(moment.month - 1) // 3 + 1}-{moment.year}"


def _live_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (_timestamp(row), str(row.get("decision_id") or "")))
    invoices: dict[str, dict[str, Any]] = {}
    for row in ordered:
        identity = _invoice_id(row)
        if identity:
            invoices[identity] = row
    unique = list(invoices.values())
    exceptions = [
        float(str(row["actual_action"]) != "auto_approve")
        for row in unique
        if row.get("actual_action") not in (None, "")
    ]
    midpoint = len(exceptions) // 2
    trend_value = None
    if midpoint:
        earlier = _mean(exceptions[:midpoint])
        recent = _mean(exceptions[midpoint:])
        if earlier is not None and recent is not None:
            trend_value = round(recent - earlier, 6)

    otif_values = [value for row in unique if (value := _otif(row)) is not None]
    quarterly_otif: dict[str, list[float]] = defaultdict(list)
    for row in unique:
        quarter = _quarter_key(row)
        value = _otif(row)
        if quarter is not None and value is not None:
            quarterly_otif[quarter].append(value)

    amounts = [value for row in unique if (value := _number(row.get("amount"))) is not None]
    lead_times = [
        value
        for row in unique
        if (value := _number(row.get("lead_time_days"))) is not None
    ]
    category_counts = Counter(
        str(row.get("category"))
        for row in unique
        if row.get("category") not in (None, "")
    )
    latest = unique[-1] if unique else ordered[-1]
    trend_direction = None
    if trend_value is not None:
        trend_direction = (
            "declining" if trend_value > 0 else "improving" if trend_value < 0 else "stable"
        )
    return {
        "supplier_name": latest.get("supplier_name") or latest.get("supplier"),
        "category": category_counts.most_common(1)[0][0] if category_counts else None,
        "categories": sorted(category_counts),
        "category_distribution": [
            {"category": category, "count": count}
            for category, count in category_counts.most_common()
        ],
        "exception_rate": _mean(exceptions),
        "exception_rate_trend": trend_value,
        "trend_direction": trend_direction,
        "otif": _mean(otif_values),
        "otif_by_quarter": {
            quarter: value
            for quarter, values in sorted(quarterly_otif.items())
            if (value := _mean(values)) is not None
        },
        "invoice_count": len(unique),
        "total_exceptions": int(sum(exceptions)),
        "avg_invoice_amount": _mean(amounts),
        "avg_lead_time_days": _mean(lead_times),
        "payment_terms": latest.get("payment_terms"),
        "verified_invoices": len(unique),
        "outcome_count": len(exceptions),
        "otif_observations": len(otif_values),
    }


def _fixture_value(fixture: dict[str, Any], field: str) -> Any:
    mappings = {
        "supplier_name": fixture.get("name") or fixture.get("supplier_name"),
        "category": fixture.get("category"),
        "categories": [fixture["category"]] if fixture.get("category") else [],
        "category_distribution": (
            [{"category": fixture["category"], "count": int(fixture.get("total_invoices") or 0)}]
            if fixture.get("category")
            else []
        ),
        "exception_rate": _number(fixture.get("exception_rate")),
        "exception_rate_trend": None,
        "trend_direction": fixture.get("recent_trend"),
        "otif": _number(fixture.get("otif_score", fixture.get("otif"))),
        "otif_by_quarter": fixture.get("quarterly_otif"),
        "invoice_count": int(_number(fixture.get("total_invoices")) or 0),
        "total_exceptions": int(_number(fixture.get("total_exceptions")) or 0),
        "avg_invoice_amount": _number(fixture.get("avg_invoice_amount")),
        "avg_lead_time_days": _number(fixture.get("lead_time")),
        "payment_terms": fixture.get("payment_terms"),
    }
    return mappings[field]


def _profile(
    supplier_id: str,
    fixture: dict[str, Any],
    live_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    live = _live_metrics(live_rows) if live_rows else {}
    fields = (
        "supplier_name",
        "category",
        "categories",
        "category_distribution",
        "exception_rate",
        "exception_rate_trend",
        "trend_direction",
        "otif",
        "otif_by_quarter",
        "invoice_count",
        "total_exceptions",
        "avg_invoice_amount",
        "avg_lead_time_days",
        "payment_terms",
    )
    result: dict[str, Any] = {"supplier_id": supplier_id}
    field_provenance: dict[str, str] = {"supplier_id": "graph" if live_rows else "sample_fallback"}
    for field in fields:
        live_value = live.get(field)
        if live_value not in (None, [], {}):
            result[field] = live_value
            field_provenance[field] = "graph"
            continue
        fixture_value = _fixture_value(fixture, field)
        if fixture_value not in (None, [], {}):
            result[field] = fixture_value
            field_provenance[field] = "sample_fallback"
        else:
            field_provenance[field] = "unavailable"

    name = str(result.get("supplier_name") or supplier_id)
    result.update(
        name=name,
        supplier_name=name,
        otif_score=result.get("otif"),
        quarterly_otif=result.get("otif_by_quarter", {}),
        total_invoices=int(result.get("invoice_count") or 0),
        recent_trend=result.get("trend_direction"),
        provenance="graph" if live_rows else "sample_fallback",
        source="graph" if live_rows else "sample_fallback",
        field_provenance=field_provenance,
        unavailable_fields=list(_UNAVAILABLE_FIELDS),
        region_available=False,
        financial_health_trend_available=False,
        pricing_trend_available=False,
        payment_response_available=False,
        verified_invoices=int(live.get("verified_invoices") or 0),
        outcome_count=int(live.get("outcome_count") or 0),
        otif_observations=int(live.get("otif_observations") or 0),
    )
    field_provenance.update({field: "unavailable" for field in _UNAVAILABLE_FIELDS})
    if isinstance(fixture.get("lead_time"), dict):
        result["lead_time"] = dict(fixture["lead_time"])
        field_provenance["lead_time"] = "sample_fallback"
    for field in (
        "behavioral_scores",
        "category_exception_rates",
        "monthly_volume",
    ):
        if field in fixture:
            result[field] = fixture[field]
            field_provenance[field] = "sample_fallback"
    return result


def build_profiles(
    verified_decisions: list[dict[str, Any]],
    fixture_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Merge verified graph rows with fixture master data without double counting."""
    fixture_rows = _fixture_rows(fixture_path)
    fixtures = {_supplier_id(row): row for row in fixture_rows if _supplier_id(row)}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for original in verified_decisions:
        row = _normalized(original)
        supplier_id = _supplier_id(row)
        if supplier_id and _verified(row):
            grouped[supplier_id].append(row)

    ordered_ids = [supplier_id for row in fixture_rows if (supplier_id := _supplier_id(row))]
    ordered_ids.extend(sorted(set(grouped) - set(ordered_ids)))
    return [_profile(supplier_id, fixtures.get(supplier_id, {}), grouped.get(supplier_id, []))
            for supplier_id in ordered_ids]


def build_profiles_from_store(
    store: Any,
    fixture_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Build profiles from one current GraphStore decision/outcome view."""
    decisions = store.get_all_decisions("s2p")
    outcomes = store.get_verified_decisions("s2p")
    outcome_by_id = {
        row.get("decision_id"): row
        for row in outcomes
        if isinstance(row, dict) and row.get("decision_id") is not None
    }
    merged = [
        {**row, **outcome_by_id.get(row.get("decision_id"), {})}
        for row in decisions
        if isinstance(row, dict)
        and row.get("domain", "s2p") == "s2p"
        and not row.get("archived")
    ]
    return build_profiles(merged, fixture_path=fixture_path)


def preview_profiles(
    profiles: list[dict[str, Any]],
    fixture_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return exactly the canonical fixture suppliers in fixture order."""
    by_id = {str(profile.get("supplier_id")): profile for profile in profiles}
    return [by_id[supplier_id] for supplier_id in fixture_supplier_ids(fixture_path)]
