"""Receipt-based financial impact aggregation for S2P."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


@dataclass
class FinancialSummary:
    total_decisions: int = 0
    verified_decisions: int = 0
    total_amount: float = 0.0
    total_at_risk: float = 0.0
    total_recovered: float = 0.0
    net_savings: float = 0.0
    recovery_rate: float = 0.0
    missing_receipts: int = 0
    by_supplier: dict[str, dict[str, float | int]] = field(default_factory=dict)
    by_category: dict[str, dict[str, float | int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _is_verified(decision: Any) -> bool:
    status = str(_get_value(decision, "status", "") or "").lower()
    if status in {"confirmed", "overridden", "verified"}:
        return True
    verified = _get_value(decision, "verified", None)
    return verified is True


def _receipt_key(receipt: Any) -> str | None:
    return _get_value(receipt, "decision_id") or _get_value(receipt, "invoice_id")


def _decision_key(decision: Any) -> str | None:
    return _get_value(decision, "decision_id") or _get_value(decision, "invoice_id")


def _bucket_add(
    buckets: dict[str, dict[str, float | int]],
    key: str | None,
    *,
    amount: float,
    at_risk: float,
    recovered: float,
) -> None:
    bucket = buckets.setdefault(
        key or "unknown",
        {"count": 0, "amount": 0.0, "at_risk": 0.0, "recovered": 0.0},
    )
    bucket["count"] = int(bucket["count"]) + 1
    bucket["amount"] = float(bucket["amount"]) + amount
    bucket["at_risk"] = float(bucket["at_risk"]) + at_risk
    bucket["recovered"] = float(bucket["recovered"]) + recovered


def compute_financial_impact(
    decisions: Iterable[Any],
    receipts: Iterable[Any] | None = None,
) -> FinancialSummary:
    """Aggregate verified S2P financial impact from decisions and receipts."""
    decision_list = list(decisions)
    receipt_by_key = {
        str(key): receipt
        for receipt in (receipts or [])
        if (key := _receipt_key(receipt)) is not None
    }

    summary = FinancialSummary(total_decisions=len(decision_list))
    for decision in decision_list:
        if not _is_verified(decision):
            continue

        decision_key = _decision_key(decision)
        receipt = receipt_by_key.get(str(decision_key)) if decision_key is not None else None
        if receipt is None:
            summary.missing_receipts += 1

        amount = _as_float(_get_value(receipt, "amount", _get_value(decision, "amount")))
        at_risk = _as_float(
            _get_value(receipt, "amount_at_risk", _get_value(decision, "amount_at_risk"))
        )
        recovered = _as_float(
            _get_value(receipt, "amount_recovered", _get_value(decision, "amount_recovered"))
        )
        supplier = _get_value(receipt, "supplier_name", _get_value(decision, "supplier_name"))
        category = _get_value(receipt, "category", _get_value(decision, "category"))

        summary.verified_decisions += 1
        summary.total_amount += amount
        summary.total_at_risk += at_risk
        summary.total_recovered += recovered
        _bucket_add(summary.by_supplier, supplier, amount=amount, at_risk=at_risk, recovered=recovered)
        _bucket_add(summary.by_category, category, amount=amount, at_risk=at_risk, recovered=recovered)

    summary.net_savings = summary.total_recovered
    summary.recovery_rate = (
        summary.total_recovered / summary.total_at_risk if summary.total_at_risk else 0.0
    )
    return summary
