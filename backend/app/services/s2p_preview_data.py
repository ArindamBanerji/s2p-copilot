"""Read S2P decision history and derive preview profiles without cached state."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.graph.s2p_graph_reader import S2PGraphReader


_DEMO_SUPPLIERS_PATH = Path(__file__).resolve().parents[3] / "data" / "s2p_demo_suppliers.json"


def demo_supplier_profiles() -> list[dict[str, Any]]:
    """Read the preview catalogue, isolated from scored/live supplier history.

    Keep fixture provenance and fields intact. Missing or malformed fixtures
    must surface as errors, never switch the preview to the live graph.
    """
    rows = json.loads(_DEMO_SUPPLIERS_PATH.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Demo supplier fixture must be a list of supplier objects")
    return [{**row, **_preview_supplier(row)} for row in rows]


def _verified_rows(
    store: Any, *, return_status: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], bool]:
    try:
        rows = S2PGraphReader(store).get_verified_decisions()
    except (OSError, RuntimeError):
        result: list[dict[str, Any]] = []
        return (result, False) if return_status else result
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        result = []
        return (result, False) if return_status else result
    result = [dict(row) for row in rows]
    return (result, True) if return_status else result


def clear_decision_cache() -> None:
    """Compatibility hook: decision history is no longer cached here."""


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


def _decision_data(row: dict[str, Any]) -> dict[str, Any]:
    outcome = _mapping(row.get("outcome_metadata"))
    return {**_mapping(row.get("metadata")), **row,
            **outcome, **_mapping(outcome.get("context")), **_mapping(row.get("context"))}


def _timestamp(row: dict[str, Any]) -> float:
    value = row.get("created_at") or row.get("timestamp_epoch") or row.get("invoice_date") or 0
    try:
        return float(value)
    except (ValueError, TypeError):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()


def _verified(row: dict[str, Any]) -> bool:
    return (row.get("status") in {"confirmed", "overridden", "verified"}
            or row.get("correct") is not None or row.get("is_correct") is not None
            or row.get("verified") is True or bool(row.get("actual_action")))


def read_decisions(store: Any, *, with_outcomes: bool = False) -> list[dict[str, Any]]:
    verified = _verified_rows(store)
    if isinstance(verified, tuple):
        verified = verified[0]
    return merge_decisions(store.get_all_decisions(domain="s2p"), verified)


def merge_decisions(rows: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize one shared history/outcome snapshot without additional IO."""
    outcome_by_id = {row.get("decision_id"): row for row in outcomes if row.get("decision_id") is not None}
    return [_decision_data({**row, **outcome_by_id.get(row.get("decision_id"), {})})
            for row in rows if row.get("domain", "s2p") == "s2p" and not row.get("archived")]


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _otif(row: dict[str, Any]) -> float | None:
    # Fulfilment evidence only: prediction correctness is not delivery success.
    if row.get("otif_score") is not None:
        return float(row["otif_score"])
    if row.get("on_time") is not None and row.get("in_full") is not None:
        return float(bool(row["on_time"]) and bool(row["in_full"]))
    return None


def supplier_profiles(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in decisions:
        supplier_id = row.get("supplier_id") or row.get("supplier_entity_id")
        if supplier_id:
            groups[str(supplier_id)].append(row)
    profiles = []
    for supplier_id, history in sorted(groups.items()):
        history.sort(key=lambda row: (_timestamp(row), str(row["decision_id"])))
        # A re-score is a new decision, not an additional invoice.
        invoices = {str(row.get("invoice_id") or row.get("source_invoice_id") or row.get("entity_id") or row["decision_id"]): row
                    for row in history}
        rows = sorted(invoices.values(), key=_timestamp)
        verified_invoices = {
            str(row.get("invoice_id") or row.get("source_invoice_id") or row.get("entity_id") or row["decision_id"]): row
            for row in history if _verified(row)
        }
        verified = sorted(verified_invoices.values(), key=_timestamp)
        exceptions = [float(row["actual_action"] != "auto_approve") for row in verified if row.get("actual_action")]
        midpoint = len(exceptions) // 2
        trend = ((_mean(exceptions[midpoint:]) or 0) - (_mean(exceptions[:midpoint]) or 0)
                 if midpoint else None)
        otif = _mean([value for row in verified if (value := _otif(row)) is not None])
        quarterly_otif: dict[int, list[float]] = defaultdict(list)
        quarterly_lead: dict[int, list[float]] = defaultdict(list)
        for row in verified:
            quarter = (datetime.fromtimestamp(_timestamp(row), timezone.utc).month - 1) // 3 + 1
            observed_otif = _otif(row)
            if observed_otif is not None:
                quarterly_otif[quarter].append(observed_otif)
            if row.get("lead_time_days") is not None:
                quarterly_lead[quarter].append(float(row["lead_time_days"]))
        amounts = [float(row["amount"]) for row in rows if row.get("amount") is not None]
        latest = rows[-1]
        profile = _preview_supplier({
            "supplier_id": supplier_id,
            "name": latest.get("supplier_name") or latest.get("supplier") or supplier_id,
            "category": Counter(str(row["category"]) for row in rows).most_common(1)[0][0],
            "exception_rate": _mean(exceptions), "avg_invoice_amount": _mean(amounts),
            "payment_terms": latest.get("payment_terms"), "otif_score": otif,
            "otif": {"q1_q2": _mean(quarterly_otif[1] + quarterly_otif[2]),
                     "q3": _mean(quarterly_otif[3])},
            "total_invoices": len(rows), "total_exceptions": int(sum(exceptions)),
            "recent_trend": ("declining" if trend > 0 else "improving" if trend < 0 else "stable") if trend is not None else None,
            "region": latest.get("region"),
            "financial_health_trend": latest.get("financial_health_trend"),
            "lead_time": {"contractual": _mean([float(row["contractual_lead_time_days"]) for row in rows if row.get("contractual_lead_time_days") is not None]),
                          "actual_q4": _mean(quarterly_lead[4])},
        })
        profile.update(source="graph", verified_invoices=len(verified), outcome_count=len(exceptions),
                       otif_observations=sum(_otif(row) is not None for row in verified),
                       exception_rate_trend=trend)
        profiles.append(profile)
    return profiles


def _preview_supplier(row: dict[str, Any]) -> dict[str, Any]:
    supplier_id = str(row.get("supplier_id") or "")
    canonical_name = str(row.get("name") or row.get("supplier_name") or supplier_id)
    supplier_name = canonical_name
    exception_rate = row.get("exception_rate")
    return {
        "supplier_id": supplier_id,
        "name": canonical_name,
        "supplier_name": supplier_name,
        "category": row.get("category"),
        "exception_rate": exception_rate,
        "avg_invoice_amount": row.get("avg_invoice_amount"),
        "payment_terms": row.get("payment_terms"),
        "otif_score": row.get("otif_score"),
        "total_invoices": int(row.get("total_invoices", 0)),
        "total_exceptions": int(row.get("total_exceptions", 0)),
        "recent_trend": row.get("recent_trend"),
        # Legacy preview aliases computed from the same graph history.
        "region": row.get("region"),
        "otif": row.get("otif"),
        "lead_time": row.get("lead_time"),
        "financial_health_trend": row.get("financial_health_trend"),
    }


def invoice_process_context(store: Any, invoice_id: str) -> dict[str, Any] | None:
    """Latest recorded process evidence for this invoice, read on every request."""
    history = [
        row for row in read_decisions(store)
        if str(row.get("invoice_id") or row.get("source_invoice_id") or row.get("entity_id") or "") == invoice_id
        and row.get("process_context")
    ]
    if not history:
        return None
    latest = max(history, key=lambda row: (_timestamp(row), str(row["decision_id"])))
    return {**_mapping(latest["process_context"]), "source": "graph"}
