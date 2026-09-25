"""S2P-specific VLD evidence provider."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from copilot_sdk.scoring.investigation import EvidenceProvider

try:
    from app.domains.s2p.config import S2PDomainConfig
except ModuleNotFoundError:
    from backend.app.domains.s2p.config import S2PDomainConfig


DATA_ROOT = Path(__file__).resolve().parents[3] / "data"


class S2PEvidenceProvider:
    """Read S2P supplier, contract, receipt, pricing, and compliance evidence."""

    def __init__(self, data_source: dict[str, Any] | str | Path | None = None, invoice_id: str | None = None) -> None:
        self.data_source = _load_data_source(data_source)
        self.invoice_id = str(invoice_id or "")

    def read_evidence(
        self,
        decision_id: str,
        dimension: int,
        factor_name: str,
    ) -> dict[str, Any] | None:
        invoice_id = self.invoice_id or str(decision_id)
        invoice = self._invoice(invoice_id)
        if invoice is None:
            return None
        canonical = _canonical_factor(factor_name)
        if canonical is None:
            return None
        supplier = self._supplier(invoice)
        contract = self._contract(invoice)
        lookup = {
            "match_status": self._receipt_or_contract_evidence,
            "amount_variance_ratio": self._pricing_evidence,
            "duplicate_score": self._duplicate_evidence,
            "supplier_exception_history": self._supplier_history_evidence,
            "payment_terms_impact": self._payment_terms_evidence,
            "commodity_index_correlation": self._commodity_evidence,
            "tax_regulatory_compliance": self._compliance_evidence,
            "environmental_risk": self._environmental_evidence,
        }[canonical]
        payload = lookup(invoice, supplier, contract)
        if payload is None:
            return None
        payload.setdefault("dimension", int(dimension))
        payload.setdefault("factor_name", canonical)
        payload.setdefault("invoice_id", invoice_id)
        return payload

    def _invoice(self, invoice_id: str) -> dict[str, Any] | None:
        invoices = self.data_source.get("invoices", {})
        invoice = invoices.get(invoice_id)
        return deepcopy(invoice) if isinstance(invoice, dict) else None

    def _supplier(self, invoice: dict[str, Any]) -> dict[str, Any]:
        supplier_id = str(invoice.get("supplier_id") or "")
        supplier = self.data_source.get("suppliers", {}).get(supplier_id)
        return deepcopy(supplier) if isinstance(supplier, dict) else {}

    def _contract(self, invoice: dict[str, Any]) -> dict[str, Any]:
        contract_ref = str(invoice.get("metadata", {}).get("contract_ref") or invoice.get("contract_ref") or "")
        contracts = self.data_source.get("contracts", {})
        contract = contracts.get(contract_ref)
        if isinstance(contract, dict):
            return deepcopy(contract)
        supplier_id = str(invoice.get("supplier_id") or "")
        for candidate in contracts.values():
            if isinstance(candidate, dict) and candidate.get("supplier_id") == supplier_id:
                return deepcopy(candidate)
        return {}

    def _showcase(self, invoice: dict[str, Any], key: str) -> dict[str, Any] | None:
        block = invoice.get("vld_evidence")
        if isinstance(block, dict) and isinstance(block.get(key), dict):
            return deepcopy(block[key])
        return None

    def _supplier_history_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        showcase = self._showcase(invoice, "supplier_history")
        if showcase:
            verified = int(showcase.get("verified_priors", 0) or 0)
            total = int(showcase.get("total_priors", verified) or verified)
            trust_score = float(showcase.get("trust_score", 0.82) or 0.82)
            exception_value = float(showcase.get("exception_value", 1.0 - trust_score) or (1.0 - trust_score))
            return {
                "value": _clamp(exception_value),
                "confidence": _sample_confidence(verified, total),
                "source": "supplier_history",
                "trust_score": _clamp(trust_score),
                "verified_priors": verified,
                "partial_delivery_pricing_error_ratio": float(showcase.get("partial_delivery_pricing_error_ratio", 0.0) or 0.0),
            }
        if not supplier:
            return None
        total = int(supplier.get("total_invoices") or 0)
        exceptions = int(supplier.get("total_exceptions") or round(total * float(supplier.get("exception_rate", 0.5) or 0.5)))
        exception_rate = exceptions / total if total > 0 else float(supplier.get("exception_rate", 0.5) or 0.5)
        return {
            "value": _clamp(exception_rate),
            "confidence": _sample_confidence(total, max(total, 1)),
            "source": "supplier_history",
            "trust_score": _clamp(1.0 - exception_rate),
            "verified_priors": total,
        }

    def _receipt_or_contract_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        showcase = self._showcase(invoice, "contract_coverage")
        if showcase:
            return {
                "value": _clamp(showcase.get("coverage_score", 0.91)),
                "confidence": _clamp(showcase.get("confidence", 0.9)),
                "source": "contract_db",
                "contract_ref": showcase.get("contract_ref") or contract.get("contract_ref"),
                "clause": showcase.get("clause"),
                "coverage_score": _clamp(showcase.get("coverage_score", 0.91)),
            }
        if contract:
            return {
                "value": _clamp(contract.get("coverage_score", 0.75)),
                "confidence": _clamp(contract.get("confidence", 0.85)),
                "source": "contract_db",
                "contract_ref": contract.get("contract_ref"),
                "clause": contract.get("clause"),
            }
        factors = invoice.get("factors") if isinstance(invoice.get("factors"), dict) else {}
        if "match_status" in factors:
            return {"value": _clamp(factors["match_status"]), "confidence": 0.92, "source": "receipt_system"}
        return None

    def _pricing_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        showcase = self._showcase(invoice, "pricing_benchmark")
        if showcase:
            return {
                "value": _clamp(showcase.get("variance_normalized", showcase.get("value", 0.5))),
                "confidence": _clamp(showcase.get("confidence", 0.8)),
                "source": "pricing_benchmark",
                "benchmark_delta_pct": float(showcase.get("benchmark_delta_pct", 0.0) or 0.0),
                "sample_size": int(showcase.get("sample_size", 0) or 0),
            }
        factors = invoice.get("factors") if isinstance(invoice.get("factors"), dict) else {}
        if "amount_variance_ratio" in factors:
            return {
                "value": _clamp(factors["amount_variance_ratio"]),
                "confidence": 0.75,
                "source": "pricing_benchmark",
            }
        return None

    def _commodity_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        showcase = self._showcase(invoice, "volume_anomaly")
        if showcase:
            return {
                "value": _clamp(showcase.get("contract_context_score", showcase.get("value", 0.8))),
                "confidence": _clamp(showcase.get("confidence", 0.75)),
                "source": "demand_forecast",
                "volume_multiplier": float(showcase.get("volume_multiplier", 1.0) or 1.0),
                "explanation": showcase.get("explanation"),
            }
        monthly = supplier.get("monthly_volume")
        if isinstance(monthly, list) and monthly:
            current = float(monthly[-1] or 0.0)
            baseline = sum(float(item or 0.0) for item in monthly[:-1]) / max(len(monthly) - 1, 1)
            ratio = current / baseline if baseline > 0 else 1.0
            return {
                "value": _clamp(1.0 - abs(ratio - 1.0)),
                "confidence": 0.75,
                "source": "demand_forecast",
                "volume_multiplier": ratio,
            }
        return None

    def _duplicate_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        factors = invoice.get("factors") if isinstance(invoice.get("factors"), dict) else {}
        if "duplicate_score" not in factors:
            return None
        return {
            "value": _clamp(factors["duplicate_score"]),
            "confidence": 0.82,
            "source": "invoice_history",
        }

    def _payment_terms_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        terms = str(supplier.get("payment_terms") or contract.get("payment_terms") or "")
        if not terms:
            return None
        score = 0.82 if "45" in terms or "60" in terms else 0.65
        return {
            "value": score,
            "confidence": 0.8,
            "source": "payment_terms",
            "payment_terms": terms,
        }

    def _compliance_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        showcase = self._showcase(invoice, "compliance")
        if showcase:
            return {
                "value": _clamp(showcase.get("score", 0.9)),
                "confidence": _clamp(showcase.get("confidence", 0.8)),
                "source": "compliance_db",
                "flags": list(showcase.get("flags") or []),
            }
        if supplier:
            score = float(supplier.get("behavioral_scores", {}).get("quality_score", 0.8) or 0.8)
            return {"value": _clamp(score), "confidence": 0.8, "source": "compliance_db", "flags": []}
        return None

    def _environmental_evidence(
        self,
        invoice: dict[str, Any],
        supplier: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any] | None:
        showcase = self._showcase(invoice, "environmental")
        if showcase:
            return {
                "value": _clamp(showcase.get("score", 0.5)),
                "confidence": _clamp(showcase.get("confidence", 0.7)),
                "source": "compliance_db",
            }
        if supplier:
            return {"value": 0.5, "confidence": 0.65, "source": "compliance_db"}
        return None


def load_s2p_vld_data(path: str | Path | None = None) -> dict[str, Any]:
    source = Path(path) if path is not None else None
    if source is not None and source.exists():
        payload = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return _normalize_data_source(payload)
    invoices = _load_list(DATA_ROOT / "synthetic_invoices.json")
    suppliers = _load_list(DATA_ROOT / "s2p_demo_suppliers.json")
    return _normalize_data_source({"invoices": invoices, "suppliers": suppliers, "contracts": []})


def _load_data_source(data_source: dict[str, Any] | str | Path | None) -> dict[str, Any]:
    if data_source is None:
        return load_s2p_vld_data()
    if isinstance(data_source, (str, Path)):
        return load_s2p_vld_data(data_source)
    return _normalize_data_source(data_source)


def _normalize_data_source(payload: dict[str, Any]) -> dict[str, Any]:
    invoices = payload.get("invoices") or payload.get("showcase_invoices") or []
    suppliers = payload.get("suppliers") or payload.get("supplier_profiles") or []
    contracts = payload.get("contracts") or []
    return {
        "invoices": _index(invoices, "invoice_id"),
        "suppliers": _index(suppliers, "supplier_id"),
        "contracts": _index(contracts, "contract_ref"),
    }


def _load_list(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _index(rows: Any, key: str) -> dict[str, dict[str, Any]]:
    if isinstance(rows, dict):
        return {str(k): deepcopy(v) for k, v in rows.items() if isinstance(v, dict)}
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get(key)): deepcopy(row)
        for row in rows
        if isinstance(row, dict) and row.get(key) is not None
    }


def _canonical_factor(factor_name: str) -> str | None:
    aliases = {
        "supplier_trust": "supplier_exception_history",
        "contract_coverage": "match_status",
        "pricing_variance": "amount_variance_ratio",
        "delivery_pattern": "supplier_exception_history",
        "receipt_match": "match_status",
        "compliance_risk": "tax_regulatory_compliance",
        "volume_anomaly": "commodity_index_correlation",
    }
    name = aliases.get(str(factor_name), str(factor_name))
    return name if name in S2PDomainConfig.factors else None


def _sample_confidence(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(max(0.1, min(1.0, numerator / denominator)))


def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


__all__ = ["S2PEvidenceProvider", "load_s2p_vld_data"]
