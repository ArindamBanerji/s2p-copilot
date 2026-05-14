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
