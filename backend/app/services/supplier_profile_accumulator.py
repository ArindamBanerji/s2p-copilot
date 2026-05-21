"""In-memory supplier profile accumulation for S2P verified decisions."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date
import json
from pathlib import Path
from typing import Any


TRAILING_WINDOW = 200
COMPUTED_THRESHOLD = 20
TREND_MIN_POINTS = 10


@dataclass
class SupplierProfile:
    supplier_id: str
    supplier_name: str
    exception_rate: float
    exception_rate_trend: float | None = None
    otif: float | None = None
    otif_by_quarter: dict[str, float] = field(default_factory=dict)
    avg_lead_time_days: float | None = None
    lead_time_by_quarter: dict[str, float] = field(default_factory=dict)
    invoice_count: int = 0
    last_invoice_date: str | None = None
    pricing_trend: float | None = None
    categories: list[str] = field(default_factory=list)
    last_updated: str | None = None
    source: str = "fixture"


@dataclass
class SupplierEvent:
    supplier_id: str
    supplier_name: str
    invoice_id: str
    invoice_date: str | None
    amount: float
    category: str
    recommended_action: str
    actual_action: str
    is_correct: bool
    reward: float
    factors: dict[str, float]
    timestamp: str


class SupplierProfileAccumulator:
    """Accumulates per-supplier behavior from verified S2P decisions."""

    def __init__(self, fixture_path: str | None = None) -> None:
        self._fixture_profiles: dict[str, SupplierProfile] = {}
        self._events: dict[str, deque[SupplierEvent]] = defaultdict(lambda: deque(maxlen=TRAILING_WINDOW))
        self.skipped_missing_supplier_id = 0
        if fixture_path is not None:
            self._load_fixtures(fixture_path)

    def _load_fixtures(self, path: str) -> None:
        fixture_path = Path(path)
        try:
            raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = []
        if not isinstance(raw, list):
            return
        for row in raw:
            if not isinstance(row, dict):
                continue
            supplier_id = str(row.get("supplier_id") or "").strip()
            if not supplier_id:
                continue
            supplier_name = str(row.get("name") or row.get("supplier_name") or supplier_id)
            category = row.get("category")
            categories = [str(category)] if category not in (None, "") else []
            self._fixture_profiles[supplier_id] = SupplierProfile(
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                exception_rate=_to_float(row.get("exception_rate"), 0.0),
                exception_rate_trend=_numeric_or_none(row.get("recent_trend")),
                otif=_numeric_or_none(row.get("otif_score", row.get("otif"))),
                invoice_count=int(_to_float(row.get("total_invoices", row.get("invoice_count")), 0.0)),
                categories=categories,
                source="fixture",
            )

    def on_decision_verified(
        self,
        decision: dict[str, Any],
        outcome: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> None:
        event = self._extract_event(decision, outcome, context or {})
        if event is None:
            self.skipped_missing_supplier_id += 1
            return
        self._events[event.supplier_id].append(event)

    def get_profile(self, supplier_id: str) -> SupplierProfile | None:
        supplier_id = str(supplier_id)
        events = list(self._events.get(supplier_id, ()))
        fixture = self._fixture_profiles.get(supplier_id)
        if not events:
            return _copy_profile(fixture) if fixture is not None else None
        if len(events) < COMPUTED_THRESHOLD and fixture is not None:
            profile = _copy_profile(fixture)
            profile.invoice_count = fixture.invoice_count + len(events)
            profile.last_invoice_date = _latest_invoice_date(events)
            profile.last_updated = _latest_timestamp(events)
            profile.categories = _merge_categories(fixture.categories, self._compute_categories(events))
            profile.source = "fixture"
            return profile
        return self._profile_from_events(supplier_id, events, fixture)

    def get_all_profiles(self) -> list[SupplierProfile]:
        supplier_ids = sorted(set(self._fixture_profiles) | set(self._events))
        return [profile for supplier_id in supplier_ids if (profile := self.get_profile(supplier_id)) is not None]

    def get_declining_suppliers(self, min_trend: float = 0.0) -> list[SupplierProfile]:
        profiles = [
            profile
            for profile in self.get_all_profiles()
            if profile.exception_rate_trend is not None and profile.exception_rate_trend > min_trend
        ]
        return sorted(profiles, key=lambda profile: profile.exception_rate_trend or 0.0, reverse=True)

    def get_supplier_history(self, supplier_id: str, limit: int = TRAILING_WINDOW) -> list[dict[str, Any]]:
        events = list(self._events.get(str(supplier_id), ()))
        safe_limit = max(int(limit), 0)
        if safe_limit == 0:
            return []
        return [asdict(event) for event in events[-safe_limit:]]

    def reset(self) -> None:
        self._events.clear()
        self.skipped_missing_supplier_id = 0

    def _extract_event(
        self,
        decision: dict[str, Any],
        outcome: dict[str, Any],
        context: dict[str, Any],
    ) -> SupplierEvent | None:
        metadata = _metadata(decision)
        outcome_metadata = _metadata(outcome)
        supplier_id = _first_text((metadata, context), ("supplier_id", "supplierId"))
        if not supplier_id:
            return None
        supplier_name = (
            _first_text((metadata, context), ("supplier_name", "supplierName", "supplier"))
            or supplier_id
        )
        invoice_id = (
            _first_text((metadata, context), ("invoice_id", "source_invoice_id", "invoiceId"))
            or str(decision.get("entity_id") or decision.get("decision_id") or "")
        )
        invoice_date = _first_text((metadata, context), ("invoice_date", "invoiceDate"))
        amount = _to_float(_first_value((metadata, context), ("amount", "total_amount")), 0.0)
        category = str(decision.get("category") or context.get("category") or metadata.get("category") or "unknown")
        recommended_action = str(
            decision.get("recommended_action")
            or decision.get("action")
            or metadata.get("recommended_action")
            or ""
        )
        actual_action = str(
            outcome.get("actual_action")
            or outcome_metadata.get("actual_action")
            or context.get("actual_action")
            or ""
        )
        is_correct = bool(outcome.get("is_correct", outcome_metadata.get("is_correct", False)))
        reward = _to_float(outcome.get("reward", outcome_metadata.get("reward")), 0.0)
        return SupplierEvent(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            invoice_id=invoice_id,
            invoice_date=invoice_date,
            amount=amount,
            category=category,
            recommended_action=recommended_action,
            actual_action=actual_action,
            is_correct=is_correct,
            reward=reward,
            factors=_extract_factors(decision),
            timestamp=_first_text(
                (outcome, outcome_metadata, decision, metadata),
                ("timestamp", "verified_at", "decision_time", "invoice_date"),
            ) or "",
        )

    def _profile_from_events(
        self,
        supplier_id: str,
        events: list[SupplierEvent],
        fixture: SupplierProfile | None,
    ) -> SupplierProfile:
        supplier_name = events[-1].supplier_name or (fixture.supplier_name if fixture else supplier_id)
        otif = fixture.otif if fixture is not None else None
        source = "hybrid" if otif is not None else "computed"
        return SupplierProfile(
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            exception_rate=self._compute_exception_rate(events),
            exception_rate_trend=self._compute_trend(events),
            otif=otif,
            otif_by_quarter={},
            avg_lead_time_days=None,
            lead_time_by_quarter={},
            invoice_count=len(events),
            last_invoice_date=_latest_invoice_date(events),
            pricing_trend=self._compute_pricing_trend(events),
            categories=self._compute_categories(events),
            last_updated=_latest_timestamp(events),
            source=source,
        )

    def _compute_exception_rate(self, events: list[SupplierEvent]) -> float:
        if not events:
            return 0.0
        exceptions = sum(1 for event in events if not event.is_correct)
        return exceptions / len(events)

    def _compute_trend(self, events: list[SupplierEvent]) -> float | None:
        dated = [
            (parsed, 0.0 if event.is_correct else 1.0)
            for event in events
            if (parsed := _parse_date(event.invoice_date)) is not None
        ]
        if len(dated) < TREND_MIN_POINTS:
            return None
        start = min(day for day, _ in dated)
        xs = [(day - start).days for day, _ in dated]
        ys = [value for _, value in dated]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator == 0:
            return None
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        return numerator / denominator

    def _compute_quarterly(self, events: list[SupplierEvent], field: str = "exception_rate") -> dict[str, float]:
        buckets: dict[str, list[SupplierEvent]] = defaultdict(list)
        for event in events:
            parsed = _parse_date(event.invoice_date)
            if parsed is None:
                continue
            quarter = f"Q{((parsed.month - 1) // 3) + 1}"
            buckets[quarter].append(event)
        if not buckets:
            return {}
        if field != "exception_rate":
            return {}
        return {
            quarter: self._compute_exception_rate(items)
            for quarter, items in sorted(buckets.items())
        }

    def _compute_categories(self, events: list[SupplierEvent]) -> list[str]:
        return sorted({event.category for event in events if event.category and event.category != "unknown"})

    def _compute_pricing_trend(self, events: list[SupplierEvent]) -> float | None:
        dated = [
            (parsed, event.amount)
            for event in events
            if event.amount and (parsed := _parse_date(event.invoice_date)) is not None
        ]
        if len(dated) < TREND_MIN_POINTS:
            return None
        start = min(day for day, _ in dated)
        xs = [(day - start).days for day, _ in dated]
        ys = [amount for _, amount in dated]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        if denominator == 0:
            return None
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        return numerator / denominator


def _default_fixture_path() -> str:
    return str(Path(__file__).resolve().parents[3] / "data" / "s2p_demo_suppliers.json")


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _first_value(sources: tuple[dict[str, Any], ...], keys: tuple[str, ...]) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def _first_text(sources: tuple[dict[str, Any], ...], keys: tuple[str, ...]) -> str | None:
    value = _first_value(sources, keys)
    return str(value) if value not in (None, "") else None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _numeric_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_factors(decision: dict[str, Any]) -> dict[str, float]:
    factors = decision.get("factors")
    if isinstance(factors, dict):
        return {str(key): _to_float(value, 0.0) for key, value in factors.items()}
    metadata = _metadata(decision)
    vector = metadata.get("factor_vector")
    if isinstance(vector, list):
        return {
            f"factor_{index}": _to_float(value, 0.0)
            for index, value in enumerate(vector)
        }
    return {}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _latest_invoice_date(events: list[SupplierEvent]) -> str | None:
    dates = [event.invoice_date for event in events if _parse_date(event.invoice_date) is not None]
    return max(dates) if dates else None


def _latest_timestamp(events: list[SupplierEvent]) -> str | None:
    timestamps = [event.timestamp for event in events if event.timestamp]
    return timestamps[-1] if timestamps else None


def _merge_categories(left: list[str], right: list[str]) -> list[str]:
    return sorted({*left, *right})


def _copy_profile(profile: SupplierProfile | None) -> SupplierProfile | None:
    if profile is None:
        return None
    return SupplierProfile(
        supplier_id=profile.supplier_id,
        supplier_name=profile.supplier_name,
        exception_rate=profile.exception_rate,
        exception_rate_trend=profile.exception_rate_trend,
        otif=profile.otif,
        otif_by_quarter=dict(profile.otif_by_quarter),
        avg_lead_time_days=profile.avg_lead_time_days,
        lead_time_by_quarter=dict(profile.lead_time_by_quarter),
        invoice_count=profile.invoice_count,
        last_invoice_date=profile.last_invoice_date,
        pricing_trend=profile.pricing_trend,
        categories=list(profile.categories),
        last_updated=profile.last_updated,
        source=profile.source,
    )


accumulator = SupplierProfileAccumulator(fixture_path=_default_fixture_path())
