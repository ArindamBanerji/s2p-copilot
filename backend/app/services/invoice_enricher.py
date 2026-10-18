"""Enrich queue records from synthetic_invoices.json for sample IDs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURE_CACHE: dict[str, dict[str, Any]] | None = None
_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "data" / "synthetic_invoices.json"


def _load_fixtures() -> dict[str, dict[str, Any]]:
    global _FIXTURE_CACHE
    if _FIXTURE_CACHE is None:
        _FIXTURE_CACHE = {}
        if _FIXTURE_PATH.exists():
            rows = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    inv_id = row.get("invoice_id") or row.get("event_id") or row.get("id")
                    if inv_id:
                        _FIXTURE_CACHE[str(inv_id)] = row
    return _FIXTURE_CACHE


def enrich_from_fixture(record: dict[str, Any]) -> dict[str, Any]:
    """Merge fixture fields into a queue record for sample invoice IDs.

    Only enrich fields that are ``None`` or missing. The fixture contributes
    labeled sample data; it never overwrites values read from the live graph.
    """
    inv_id = record.get("invoice_id") or record.get("event_id") or record.get("decision_id")
    if not inv_id:
        return record

    fixture = _load_fixtures().get(str(inv_id))
    if fixture is None:
        return record

    enriched = dict(record)
    enriched["provenance"] = "sample"

    metadata = fixture.get("metadata")
    fixture_metadata = metadata if isinstance(metadata, dict) else {}
    for key in ("po_number", "po_reference", "invoice_date", "due_date"):
        if enriched.get(key) is None:
            value = fixture.get(key) or fixture_metadata.get(key)
            if value is not None:
                enriched[key] = value

    if enriched.get("ground_truth_action") is None:
        ground_truth = fixture.get("ground_truth_action") or fixture.get("actual_action")
        if ground_truth is not None:
            enriched["ground_truth_action"] = ground_truth

    existing_metadata = enriched.get("metadata")
    merged_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
    for key, value in fixture_metadata.items():
        if merged_metadata.get(key) is None and value is not None:
            merged_metadata[key] = value
    enriched["metadata"] = merged_metadata

    fixture_factors = fixture.get("factors")
    if isinstance(fixture_factors, dict):
        existing_factors = enriched.get("factors")
        merged_factors = dict(existing_factors) if isinstance(existing_factors, dict) else {}
        for key, value in fixture_factors.items():
            if merged_factors.get(key) is None and value is not None:
                merged_factors[key] = value
        enriched["factors"] = merged_factors

    return enriched
