"""Shared S2P fixture loaders used by backend routers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def load_json(name: str, default: Any) -> Any:
    try:
        return json.loads((_DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def load_invoices() -> list[dict[str, Any]]:
    data = load_json("synthetic_invoices.json", [])
    return [invoice for invoice in data if isinstance(invoice, dict)] if isinstance(data, list) else []


def load_suppliers() -> list[dict[str, Any]]:
    data = load_json("s2p_demo_suppliers.json", [])
    return [supplier for supplier in data if isinstance(supplier, dict)] if isinstance(data, list) else []


def is_sample_data(record: dict[str, Any]) -> bool:
    """Check if a record is K3 demo-fixture data (Rule 67)."""
    return record.get("provenance") == "sample"


def assert_no_sample_in_metric(records: list[dict[str, Any]], metric_name: str) -> None:
    """F-26 gate: raise if sample data feeds a computed metric."""
    sample_count = sum(1 for record in records if is_sample_data(record))
    if sample_count > 0:
        raise ValueError(
            f"F-26 VIOLATION: {sample_count}/{len(records)} records "
            f"feeding metric '{metric_name}' have provenance='sample'."
        )


def find_invoice(event_id_or_invoice_id: str) -> dict[str, Any] | None:
    for invoice in load_invoices():
        if invoice.get("invoice_id") == event_id_or_invoice_id:
            return invoice
        if invoice.get("event_id") == event_id_or_invoice_id:
            return invoice
    return None
