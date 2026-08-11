"""S2P factor computers with graph-first and fixture-fallback behavior."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import logging
import re
from typing import Any, Optional, Protocol

from app.domains.s2p.config import S2PDomainConfig

log = logging.getLogger(__name__)


@dataclass
class S2PEvent:
    """Backward-compatible request event used by the existing score endpoint."""

    event_id: str
    category: str
    amount: float
    supplier_id: str
    contract_id: Optional[str] = None
    approved_categories: list = field(default_factory=list)
    supplier_risk_rating: float = 0.5
    historical_spend_mean: float = 0.0
    historical_spend_std: float = 1.0
    days_since_last_audit: int = 90
    vendor_decisions: int = 0
    vendor_approvals: int = 0

    match_status: Optional[float] = None
    amount_variance_ratio: Optional[float] = None
    duplicate_score: Optional[float] = None
    supplier_exception_history: Optional[float] = None
    payment_terms_impact: Optional[float] = None
    commodity_index_correlation: Optional[float] = None
    tax_regulatory_compliance: Optional[float] = None
    environmental_risk: Optional[float] = None


class S2PFactorComputer(Protocol):
    name: str

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        ...


def _as_invoice(invoice: dict[str, Any] | S2PEvent) -> dict[str, Any]:
    if isinstance(invoice, S2PEvent):
        data = asdict(invoice)
        data["invoice_id"] = data.get("event_id")
        data["factors"] = {
            name: data.get(name)
            for name in S2PDomainConfig.factors
            if data.get(name) is not None
        }
        return data
    return invoice


def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return float(max(0.0, min(number, 1.0)))


def _fallback(invoice: dict[str, Any], name: str, default: float) -> float:
    if name in invoice and invoice.get(name) is not None:
        return _clamp(invoice.get(name), default)
    factors = invoice.get("factors")
    if isinstance(factors, dict) and factors.get(name) is not None:
        return _clamp(factors.get(name), default)
    return _clamp(default, default)


def _neighbors(context: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if context is None:
        return []
    if isinstance(context, list):
        return [entry for entry in context if isinstance(entry, dict)]
    if isinstance(context, dict):
        raw = context.get("neighbors", [])
        if isinstance(raw, list):
            return [entry for entry in raw if isinstance(entry, dict)]
    return []


def _node(entry: dict[str, Any]) -> dict[str, Any]:
    raw = entry.get("node", entry)
    if isinstance(raw, dict):
        return raw
    return {}


def _label(node: dict[str, Any]) -> str:
    label = node.get("_label") or node.get("label") or node.get("type")
    if isinstance(label, str):
        return label
    labels = node.get("labels")
    if isinstance(labels, list) and labels:
        return str(labels[0])
    return ""


def _has_label_or_key(node: dict[str, Any], label: str, key: str) -> bool:
    node_label = _label(node)
    return node_label == label or (not node_label and node.get(key) is not None)


def _graph_has_context(context: dict[str, Any] | list[dict[str, Any]] | None) -> bool:
    return bool(_neighbors(context))


def _amount(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_get_float(value: Any) -> float | None:
    """Coerce a numeric graph property without hiding shape errors."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _record_provenance(factor: Any, provenance: str) -> None:
    factor.last_provenance = provenance


def _payment_days(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    if match:
        return int(match.group(0))
    return None


class MatchStatus:
    name = "match_status"
    last_provenance = "not_computed"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        nodes = [_node(entry) for entry in _neighbors(context)]
        po = next((node for node in nodes if _has_label_or_key(node, "PurchaseOrder", "po_id")), None)
        gr = next((node for node in nodes if _has_label_or_key(node, "GoodsReceipt", "gr_id")), None)
        if po is None and gr is None:
            approved_categories = invoice.get("approved_categories")
            if isinstance(approved_categories, list) and approved_categories:
                _record_provenance(self, "invoice_approval_match")
                return 1.0 if invoice.get("category") in approved_categories else 0.0
            _record_provenance(self, "invoice_factor_fallback")
            return _fallback(invoice, self.name, 0.5)

        if po is not None and gr is not None and any(
            gr.get(key) is not None for key in ("amount", "quantity", "qty_received")
        ) and any(po.get(key) is not None for key in ("amount", "quantity")):
            discrepancies: list[float] = []
            invoice_amount = _safe_get_float(invoice.get("amount"))
            invoice_quantity = _safe_get_float(invoice.get("quantity"))

            def compare(left: float | None, right: float | None) -> None:
                if left is not None and right is not None:
                    discrepancies.append(abs(left - right) / max(left, 1.0))

            compare(invoice_amount, _safe_get_float(po.get("amount")))
            compare(invoice_quantity, _safe_get_float(po.get("quantity")))
            compare(invoice_amount, _safe_get_float(gr.get("amount")))
            compare(invoice_quantity, _safe_get_float(gr.get("qty_received")))
            _record_provenance(self, "computed")
            return _clamp(1.0 - min(max(discrepancies), 1.0)) if discrepancies else 1.0
        if po is not None and gr is not None:
            _record_provenance(self, "purchase_order_goods_receipt_match")
            return 0.1
        if po is not None:
            _record_provenance(self, "purchase_order_without_goods_receipt")
            return 0.6
        _record_provenance(self, "goods_receipt_without_purchase_order")
        return 0.6

class AmountVarianceRatio:
    name = "amount_variance_ratio"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        inv_amount = _amount(invoice.get("amount"))
        for entry in _neighbors(context):
            node = _node(entry)
            if not _has_label_or_key(node, "PurchaseOrder", "po_id"):
                continue
            po_amount = _amount(
                node.get("amount")
                or node.get("total_amount")
                or node.get("po_amount")
                or node.get("net_amount")
            )
            if inv_amount is not None and po_amount is not None:
                return _clamp(abs(inv_amount - po_amount) / max(abs(po_amount), 1.0))

        if invoice.get("variance_pct") is not None:
            return _clamp(abs(float(invoice["variance_pct"])) / 100.0)
        if invoice.get("historical_spend_mean", 0) > 0:
            mean = float(invoice["historical_spend_mean"])
            return _clamp(abs(float(invoice.get("amount", 0.0)) - mean) / max(abs(mean), 1.0))
        return _fallback(invoice, self.name, 0.3)


class DuplicateScore:
    name = "duplicate_score"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        if _graph_has_context(context):
            current_id = invoice.get("invoice_id") or invoice.get("event_id")
            current_amount = _amount(invoice.get("amount"))
            best = 0.0
            for entry in _neighbors(context):
                node = _node(entry)
                if not _has_label_or_key(node, "Invoice", "invoice_id"):
                    continue
                if node.get("invoice_id") == current_id:
                    continue
                other_amount = _amount(node.get("amount"))
                if current_amount is None or other_amount is None:
                    continue
                denominator = max(abs(current_amount), abs(other_amount), 1.0)
                best = max(best, 1.0 - abs(current_amount - other_amount) / denominator)
            return _clamp(best, 0.0)
        return _fallback(invoice, self.name, 0.05)


class SupplierExceptionHistory:
    name = "supplier_exception_history"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        for entry in _neighbors(context):
            node = _node(entry)
            if _has_label_or_key(node, "Supplier", "supplier_id") and node.get("exception_rate") is not None:
                return _clamp(node.get("exception_rate"))
        if invoice.get("vendor_decisions", 0) > 0:
            exception_rate = 1.0 - (
                float(invoice.get("vendor_approvals", 0)) / float(invoice["vendor_decisions"])
            )
            return _clamp(exception_rate)
        if invoice.get("supplier_risk_rating") is not None:
            return _clamp(1.0 - float(invoice.get("supplier_risk_rating", 0.5)))
        return _fallback(invoice, self.name, 0.5)


class PaymentTermsImpact:
    name = "payment_terms_impact"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        actual_days = _payment_days(invoice.get("payment_days"))
        if actual_days is None:
            metadata = invoice.get("metadata")
            if isinstance(metadata, dict):
                actual_days = _payment_days(metadata.get("payment_days"))
        if actual_days is None:
            return _fallback(invoice, self.name, 0.5)

        for entry in _neighbors(context):
            node = _node(entry)
            if not _has_label_or_key(node, "Supplier", "supplier_id"):
                continue
            standard_days = _payment_days(node.get("payment_terms")) or 30
            return _clamp(abs(actual_days - standard_days) / max(standard_days, 1))
        return _fallback(invoice, self.name, 0.5)


class CommodityIndexCorrelation:
    name = "commodity_index_correlation"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        for entry in _neighbors(context):
            node = _node(entry)
            if _has_label_or_key(node, "Commodity", "commodity_id") and node.get("volatility") is not None:
                return _clamp(node.get("volatility"))
        return _fallback(invoice, self.name, 0.5)


class TaxRegulatoryCompliance:
    name = "tax_regulatory_compliance"
    last_provenance = "not_computed"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        nodes = [_node(entry) for entry in _neighbors(context)]
        contract = next(
            (node for node in nodes if _has_label_or_key(node, "Contract", "contract_id")),
            None,
        )
        if contract is None:
            if any(_has_label_or_key(node, "Supplier", "supplier_id") for node in nodes):
                _record_provenance(self, "supplier_without_contract")
                return 0.8
            metadata = invoice.get("metadata")
            if isinstance(metadata, dict) and metadata.get("tax_code"):
                _record_provenance(self, "invoice_tax_metadata")
                return 0.1
            _record_provenance(self, "invoice_factor_fallback")
            return _fallback(invoice, self.name, 0.5)

        checks_passed = 0
        checks_total = 0
        max_amount = _safe_get_float(contract.get("max_amount"))
        if max_amount is not None:
            checks_total += 1
            invoice_amount = _safe_get_float(invoice.get("amount"))
            if invoice_amount is not None and invoice_amount <= max_amount:
                checks_passed += 1

        compliant = contract.get("tax_compliant")
        if compliant is not None:
            checks_total += 1
            if compliant is True or compliant == 1 or str(compliant).lower() == "true":
                checks_passed += 1

        regulatory_status = contract.get("regulatory_status")
        if regulatory_status is not None:
            checks_total += 1
            if str(regulatory_status).lower() in {"approved", "active", "compliant"}:
                checks_passed += 1

        _record_provenance(self, "computed" if checks_total else "contract_present")
        return _clamp(checks_passed / checks_total) if checks_total else 0.15

class EnvironmentalRisk:
    name = "environmental_risk"

    def compute(
        self,
        invoice: dict[str, Any] | S2PEvent,
        context: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> float:
        invoice = _as_invoice(invoice)
        for entry in _neighbors(context):
            node = _node(entry)
            if node.get("environmental_risk") is not None:
                return _clamp(node.get("environmental_risk"))
            if node.get("carbon_footprint") is not None:
                footprint = _safe_get_float(node.get("carbon_footprint"))
                if footprint is not None:
                    return _clamp(footprint / 1_000.0)
        metadata = invoice.get("metadata")
        if isinstance(metadata, dict):
            if metadata.get("environmental_risk") is not None:
                return _clamp(metadata.get("environmental_risk"))
            if metadata.get("carbon_footprint") is not None:
                footprint = _safe_get_float(metadata.get("carbon_footprint"))
                if footprint is not None:
                    return _clamp(footprint / 1_000.0)
            if metadata.get("route_weather_risk") is not None:
                return _clamp(metadata.get("route_weather_risk"))
        return _fallback(invoice, self.name, 0.5)


ALL_FACTORS: list[S2PFactorComputer] = [
    MatchStatus(),
    AmountVarianceRatio(),
    DuplicateScore(),
    SupplierExceptionHistory(),
    PaymentTermsImpact(),
    CommodityIndexCorrelation(),
    TaxRegulatoryCompliance(),
    EnvironmentalRisk(),
]
FACTOR_NAMES = [factor.name for factor in ALL_FACTORS]

# Backward-compatible names used by existing tests and router code.
MatchStatusFactor = MatchStatus
AmountVarianceRatioFactor = AmountVarianceRatio
DuplicateScoreFactor = DuplicateScore
SupplierExceptionHistoryFactor = SupplierExceptionHistory
PaymentTermsImpactFactor = PaymentTermsImpact
CommodityIndexCorrelationFactor = CommodityIndexCorrelation
TaxRegulatoryComplianceFactor = TaxRegulatoryCompliance
S2P_FACTOR_COMPUTERS = ALL_FACTORS


def compute_all_factors(
    invoice: dict[str, Any] | S2PEvent,
    context: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """Compute all canonical S2P factors, never raising from individual factors."""
    invoice_dict = _as_invoice(invoice)
    values: dict[str, float] = {}
    for factor in ALL_FACTORS:
        try:
            value = factor.compute(invoice_dict, context)
        except Exception as exc:
            log.warning("S2P factor %s failed: %s", factor.name, exc)
            value = _fallback(invoice_dict, factor.name, 0.5)
        values[factor.name] = _clamp(value)
    return values


def compute_factor_vector(event: S2PEvent) -> list[float]:
    """Compute the canonical factor vector in S2PDomainConfig order."""
    if FACTOR_NAMES != S2PDomainConfig.factors:
        raise RuntimeError("S2P factor computer order does not match config")
    factors = compute_all_factors(event)
    return [factors[name] for name in S2PDomainConfig.factors]
