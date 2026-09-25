"""Preseed S2P VLD showcase invoices."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

try:
    from app.domains.s2p.config import S2PDomainConfig
except ModuleNotFoundError:
    from backend.app.domains.s2p.config import S2PDomainConfig

SHOWCASE_FILE = "vld_s2p_showcase.json"


def seed_vld_s2p_showcase(data_source: str | Path | dict[str, Any] | None = None) -> dict[str, Any]:
    """Create or load idempotent VLD showcase invoice evidence."""

    if isinstance(data_source, dict):
        return _merge_showcase(data_source)
    if data_source is None:
        return _merge_showcase({})
    root = Path(data_source) if data_source is not None else Path(__file__).resolve().parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    path = root / SHOWCASE_FILE
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}
    payload = _merge_showcase(existing)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def showcase_invoice(invoice_id: str) -> dict[str, Any]:
    payload = _merge_showcase({})
    for invoice in payload["invoices"]:
        if invoice["invoice_id"] == invoice_id:
            return deepcopy(invoice)
    raise KeyError(invoice_id)


def _merge_showcase(existing: dict[str, Any]) -> dict[str, Any]:
    invoices = _dedupe_by_id([*(existing.get("invoices") or []), *_showcase_invoices()], "invoice_id")
    suppliers = _dedupe_by_id([*(existing.get("suppliers") or []), *_showcase_suppliers()], "supplier_id")
    contracts = _dedupe_by_id([*(existing.get("contracts") or []), *_showcase_contracts()], "contract_ref")
    return {
        "source": "vld_s2p_showcase",
        "factor_names": list(S2PDomainConfig.factors),
        "invoices": invoices,
        "suppliers": suppliers,
        "contracts": contracts,
    }


def _showcase_invoices() -> list[dict[str, Any]]:
    return [
        {
            "invoice_id": "VLD-S2P-1",
            "supplier_id": "SUP-ASTER",
            "supplier_name": "Supplier Aster",
            "category": "price_variance",
            "amount": 47000.0,
            "surface_action": "hold_for_review",
            "expected_vld_action": "auto_approve",
            "factors": _factor_map([0.3906, 0.1186, 0.0000, 0.4267, 0.3338, 0.7039, 1.0000, 0.1912]),
            "metadata": {
                "contract_ref": "CTR-ASTER-PARTIAL",
                "scenario": "The Supplier It Knew",
                "delivery_status": "partial_delivery",
                "surface_margin_hint": 0.882886,
                "vld_narrative": (
                    "Contract coverage establishes partial delivery is covered, "
                    "then supplier history verifies low exception risk."
                ),
            },
            "vld_evidence": {
                "supplier_history": {
                    "verified_priors": 23,
                    "total_priors": 28,
                    "trust_score": 0.82,
                    "exception_value": 0.03,
                    "partial_delivery_pricing_error_ratio": 3.1,
                },
                "contract_coverage": {
                    "contract_ref": "CTR-ASTER-PARTIAL",
                    "clause": "Partial delivery may auto-approve when verified supplier history is clean.",
                    "coverage_score": 0.95,
                    "confidence": 0.90,
                },
            },
        },
        {
            "invoice_id": "VLD-S2P-2",
            "supplier_id": "SUP-MERIDIAN",
            "supplier_name": "Meridian Components",
            "category": "price_variance",
            "amount": 82000.0,
            "surface_action": "flag_leakage",
            "expected_vld_action": "auto_approve",
            "factors": _factor_map([0.9237, 0.1010, 0.0568, 0.0000, 0.6731, 0.0000, 0.3416, 0.2494]),
            "metadata": {
                "contract_ref": "CTR-MERIDIAN-BULK",
                "scenario": "Price Spike with Context",
                "price_delta_pct": 18.0,
                "volume_multiplier": 3.0,
                "surface_margin_hint": 0.890912,
                "k_dependent": True,
                "unit_sigma_behavior": (
                    "With unit sigma and no learned K, routing reads match_status then "
                    "amount_variance_ratio and remains flag_leakage."
                ),
                "learned_k_weights": [0.5, 0.5, 0.5, 0.5, 0.5, 1.0, 0.5, 0.5],
                "learned_k_behavior": (
                    "Under learned K (approximately 25 positive dim-5 updates from uniform start), "
                    "K_dim5 rises to 1.0 while other dimensions stay at baseline. VLD reads "
                    "match_status then commodity_index_correlation instead of amount_variance_ratio. "
                    "It flips flag_leakage to auto_approve."
                ),
                "vld_narrative": (
                    "K-dependent SEP-2 case: contract coverage validates the bulk pricing clause. "
                    "Learned K must then prioritize volume context over amount variance to explain the apparent price spike. "
                    "PLANTED FIXTURE: K weights in this scenario are designed to demonstrate the mechanism at "
                    "production-reachable values, not measured from operational learning. The K learning curve "
                    "experiment (§10.4) measures the actual learning dynamics."
                ),
            },
            "vld_evidence": {
                "pricing_benchmark": {
                    "benchmark_delta_pct": 18.0,
                    "variance_normalized": 0.05,
                    "confidence": 0.82,
                    "sample_size": 41,
                },
                "volume_anomaly": {
                    "volume_multiplier": 3.0,
                    "contract_context_score": 0.82,
                    "confidence": 0.78,
                    "explanation": "Rush order exceeds the 2x bulk-pricing threshold.",
                },
                "contract_coverage": {
                    "contract_ref": "CTR-MERIDIAN-BULK",
                    "clause": "Bulk pricing pass-through activates above 2x forecast volume.",
                    "coverage_score": 0.93,
                    "confidence": 0.90,
                },
            },
        },
        {
            "invoice_id": "VLD-S2P-S1",
            "supplier_id": "SUP-NORTHSTAR",
            "supplier_name": "Northstar Packaging",
            "category": "price_variance",
            "amount": 9100.0,
            "surface_action": "auto_approve",
            "expected_vld_action": "auto_approve",
            "factors": _factor_map([0.9420, 0.0716, 0.0253, 0.0800, 0.4947, 0.7893, 0.9367, 0.5000]),
            "metadata": {
                "contract_ref": "CTR-NORTHSTAR-STANDARD",
                "scenario": "S1 Conservation",
                "surface_margin_hint": 0.936492,
                "budget": 0,
            },
            "vld_evidence": {},
        },
    ]


def _showcase_suppliers() -> list[dict[str, Any]]:
    return [
        {
            "supplier_id": "SUP-ASTER",
            "name": "Supplier Aster",
            "payment_terms": "Net 45",
            "exception_rate": 0.03,
            "otif_score": 0.91,
            "total_invoices": 28,
            "total_exceptions": 1,
            "provenance": "vld_showcase",
        },
        {
            "supplier_id": "SUP-MERIDIAN",
            "name": "Meridian Components",
            "payment_terms": "Net 60",
            "exception_rate": 0.08,
            "otif_score": 0.89,
            "total_invoices": 41,
            "total_exceptions": 3,
            "monthly_volume": [20, 21, 19, 22, 20, 60],
            "provenance": "vld_showcase",
        },
        {
            "supplier_id": "SUP-NORTHSTAR",
            "name": "Northstar Packaging",
            "payment_terms": "Net 45",
            "exception_rate": 0.02,
            "otif_score": 0.97,
            "total_invoices": 80,
            "total_exceptions": 1,
            "provenance": "vld_showcase",
        },
    ]


def _showcase_contracts() -> list[dict[str, Any]]:
    return [
        {
            "contract_ref": "CTR-ASTER-PARTIAL",
            "supplier_id": "SUP-ASTER",
            "coverage_score": 0.95,
            "confidence": 0.90,
            "clause": "Partial delivery is covered for verified low-exception suppliers.",
        },
        {
            "contract_ref": "CTR-MERIDIAN-BULK",
            "supplier_id": "SUP-MERIDIAN",
            "coverage_score": 0.93,
            "confidence": 0.90,
            "clause": "Bulk pricing pass-through activates above 2x forecast volume.",
        },
        {
            "contract_ref": "CTR-NORTHSTAR-STANDARD",
            "supplier_id": "SUP-NORTHSTAR",
            "coverage_score": 0.98,
            "confidence": 0.95,
            "clause": "Standard matched invoice auto-approval.",
        },
    ]


def _factor_map(values: list[float]) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in zip(S2PDomainConfig.factors, values)
    }


def _dedupe_by_id(rows: list[Any], key: str) -> list[dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get(key):
            indexed[str(row[key])] = deepcopy(row)
    return list(indexed.values())


__all__ = ["SHOWCASE_FILE", "seed_vld_s2p_showcase", "showcase_invoice"]
