"""S2P supplier enrichment built on the GraphStore entity enrichment API."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, cast

from copilot_sdk.graph.enrichment import (
    EnrichmentSourceSet,
    EntityEnrichmentReceipt,
    EntityEnrichmentRecord,
    ProvenancedValue,
)
from copilot_sdk.graph.protocol import GraphStore

from app.routers.s2p_data_helpers import load_invoices, load_suppliers
from app.graph.s2p_graph_reader import S2PGraphReader
from app.services.lead_time import compute_lead_time_result

DOMAIN = "s2p"
ENTITY_TYPE = "Supplier"
NAMESPACE = "s2p_supplier_metrics"
COMPUTATION_VERSION = "p39b_s2p_supplier_metrics_v1"
TREND_MIN_DECISIONS = 6


class S2PSupplierEnrichmentService:
    """Compute and persist supplier metrics without mutating scorer inputs."""

    def __init__(
        self,
        *,
        graph_store: Any,
        reader: S2PGraphReader | None = None,
        suppliers: list[dict[str, Any]] | None = None,
        invoices: list[dict[str, Any]] | None = None,
    ) -> None:
        self.graph_store = graph_store
        self.reader = reader or S2PGraphReader(store=graph_store)
        self.suppliers = list(suppliers) if suppliers is not None else load_suppliers()
        self.invoices = list(invoices) if invoices is not None else load_invoices()

    def run(
        self,
        *,
        dry_run: bool = True,
        min_decisions: int = 20,
    ) -> dict[str, Any]:
        min_decisions = max(int(min_decisions), 0)
        verified = self._verified_decisions()
        all_decisions = self._all_decisions()
        supplier_ids = self._supplier_ids(verified, all_decisions)
        receipts: list[EntityEnrichmentReceipt] = []
        preview: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for supplier_id in supplier_ids:
            metrics, source_set = self.compute_supplier_metrics(
                supplier_id,
                verified_decisions=verified,
                all_decisions=all_decisions,
                min_decisions=min_decisions,
            )
            if dry_run:
                receipt = self._dry_run_receipt(supplier_id, metrics)
            else:
                receipt = self._write_metrics(
                    supplier_id=supplier_id,
                    metrics=metrics,
                    source_set=source_set,
                    dry_run=False,
                )
            receipts.append(receipt)
            if receipt.warnings:
                errors.append({"supplier_id": supplier_id, "warnings": list(receipt.warnings)})
            preview.append(
                {
                    "supplier_id": supplier_id,
                    "metrics": self.serialize_values(metrics),
                    "receipt": self.serialize_receipt(receipt),
                }
            )
        return {
            "report": {
                "domain": DOMAIN,
                "entity_type": ENTITY_TYPE,
                "namespace": NAMESPACE,
                "dry_run": bool(dry_run),
                "supplier_count": len(supplier_ids),
                "verified_decision_count": len(verified),
                "total_decision_count": len(all_decisions),
                "receipts_persisted": sum(1 for receipt in receipts if receipt.persisted),
                "errors": errors,
            },
            "receipts": [self.serialize_receipt(receipt) for receipt in receipts],
            "suppliers": preview,
        }

    def compute_supplier_metrics(
        self,
        supplier_id: str,
        *,
        verified_decisions: list[dict[str, Any]] | None = None,
        all_decisions: list[dict[str, Any]] | None = None,
        min_decisions: int = 20,
    ) -> tuple[dict[str, ProvenancedValue], EnrichmentSourceSet]:
        verified = [
            decision
            for decision in (verified_decisions if verified_decisions is not None else self._verified_decisions())
            if _supplier_id(decision) == supplier_id
        ]
        all_rows = [
            decision
            for decision in (all_decisions if all_decisions is not None else self._all_decisions())
            if _supplier_id(decision) == supplier_id
        ]
        verified_ids = [_decision_id(decision) for decision in verified if _decision_id(decision)]
        unverified_ids = [
            _decision_id(decision)
            for decision in all_rows
            if _decision_id(decision) and _decision_id(decision) not in set(verified_ids)
        ]
        source_set = EnrichmentSourceSet(
            verified_decision_count=len(verified),
            unverified_decision_count=len(unverified_ids),
            decision_ids=verified_ids + unverified_ids,
            outcome_ids=verified_ids,
            fixture_sources=["s2p_demo_suppliers.json", "synthetic_invoices.json"],
            integration_sources=["GraphStore.get_verified_decisions", "GraphStore.get_all_decisions"],
            computation_version=COMPUTATION_VERSION,
        )
        metrics: dict[str, ProvenancedValue] = {
            "verified_decisions": ProvenancedValue.from_verified(
                len(verified),
                source_count=len(verified),
                n_min=min_decisions,
                label=f"computed from {len(verified)} verified S2P outcomes",
            ),
            "unverified_decisions": ProvenancedValue(
                value=len(unverified_ids),
                source="graph_store",
                provenance_tier="context",
                source_count=len(all_rows),
                factor_eligible=False,
                provenance_label="GraphStore decision history · unverified rows",
                measured=True,
                verified=False,
                computed_at=_now(),
            ),
            "total_decisions": ProvenancedValue(
                value=len(all_rows),
                source="graph_store",
                provenance_tier="context",
                source_count=len(all_rows),
                factor_eligible=False,
                provenance_label="GraphStore decision history · total rows",
                measured=True,
                verified=False,
                computed_at=_now(),
            ),
            "exception_rate": _rate_metric(
                _exception_rate(verified),
                len(verified),
                min_decisions,
                "exception rate unavailable · no verified outcomes",
            ),
            "accuracy": _rate_metric(
                _accuracy(verified),
                len(verified),
                min_decisions,
                "accuracy unavailable · no verified outcomes",
            ),
            "category_distribution": ProvenancedValue(
                value=_category_distribution(all_rows),
                source="graph_store",
                provenance_tier="context",
                source_count=len(all_rows),
                factor_eligible=False,
                provenance_label="GraphStore decision history · includes unverified rows",
                measured=True,
                verified=False,
                computed_at=_now(),
            ),
            "top_category": ProvenancedValue(
                value=_top_category(all_rows),
                source="graph_store",
                provenance_tier="context",
                source_count=len(all_rows),
                factor_eligible=False,
                provenance_label="GraphStore decision history · includes unverified rows",
                measured=True,
                verified=False,
                computed_at=_now(),
            ),
            "decision_count_by_quarter": _quarterly_metric(
                _decision_count_by_quarter(verified),
                len(verified),
                min_decisions,
            ),
            "trend": _trend_metric(verified, min_decisions),
            "avg_lead_time_days": self._lead_time_metric(supplier_id),
            "otif_score": self._otif_metric(supplier_id),
        }
        return metrics, source_set

    def read_supplier(self, supplier_id: str) -> dict[str, ProvenancedValue]:
        if not isinstance(self.graph_store, GraphStore):
            return {}
        try:
            result = self.graph_store.read_entity_enrichment(
                domain=DOMAIN,
                entity_type=ENTITY_TYPE,
                entity_id=str(supplier_id),
                namespace=NAMESPACE,
            )
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    def list_suppliers(self, *, limit: int = 500) -> list[EntityEnrichmentRecord]:
        if not isinstance(self.graph_store, GraphStore):
            return []
        try:
            rows = self.graph_store.list_entity_enrichments(
                domain=DOMAIN, entity_type=ENTITY_TYPE, namespace=NAMESPACE, limit=limit
            )
        except Exception:
            return []
        return [row for row in rows if isinstance(row, EntityEnrichmentRecord)]

    def summary(self) -> list[dict[str, Any]]:
        by_supplier: dict[str, dict[str, ProvenancedValue]] = defaultdict(dict)
        for record in self.list_suppliers():
            by_supplier[str(record.entity_id)][str(record.metric_name)] = record.value
        rows: list[dict[str, Any]] = [
            {"supplier_id": supplier_id, "metrics": self.serialize_values(metrics)}
            for supplier_id, metrics in by_supplier.items()
        ]
        rows.sort(
            key=lambda row: _numeric_value(row["metrics"].get("exception_rate")),
            reverse=True,
        )
        return rows

    def alerts(self, *, threshold: float = 0.10) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        for row in self.summary():
            metrics = row["metrics"]
            exception = metrics.get("exception_rate", {})
            trend = metrics.get("trend", {})
            exception_alert = (
                exception.get("verified") is True
                and exception.get("measured") is True
                and _numeric_value(exception) > float(threshold)
            )
            trend_alert = (
                trend.get("verified") is True
                and trend.get("measured") is True
                and trend.get("value") == "deteriorating"
            )
            if exception_alert or trend_alert:
                alerts.append(
                    {
                        "supplier_id": row["supplier_id"],
                        "exception_rate": exception,
                        "trend": trend,
                        "reasons": [
                            reason
                            for reason, enabled in (
                                ("exception_rate_above_threshold", exception_alert),
                                ("deteriorating_verified_trend", trend_alert),
                            )
                            if enabled
                        ],
                    }
                )
        return alerts

    def _write_metrics(
        self,
        *,
        supplier_id: str,
        metrics: dict[str, ProvenancedValue],
        source_set: EnrichmentSourceSet,
        dry_run: bool,
    ) -> EntityEnrichmentReceipt:
        if not isinstance(self.graph_store, GraphStore):
            return _unsupported_receipt(supplier_id, dry_run=dry_run)
        try:
            return self.graph_store.write_entity_enrichment(
                domain=DOMAIN,
                entity_type=ENTITY_TYPE,
                entity_id=str(supplier_id),
                namespace=NAMESPACE,
                metrics=metrics,
                computed_from=source_set,
                dry_run=dry_run,
                idempotency_key=_idempotency_key(supplier_id, source_set),
            )
        except NotImplementedError as exc:
            receipt = _unsupported_receipt(supplier_id, dry_run=dry_run)
            receipt.warnings.append(str(exc))
            return receipt
        except Exception as exc:
            receipt = _unsupported_receipt(supplier_id, dry_run=dry_run)
            receipt.warnings.append(f"entity enrichment write failed: {exc}")
            return receipt

    def _dry_run_receipt(
        self,
        supplier_id: str,
        metrics: dict[str, ProvenancedValue],
    ) -> EntityEnrichmentReceipt:
        if isinstance(self.graph_store, GraphStore):
            metrics_for_dry_run = dict(metrics)
            source_set = EnrichmentSourceSet(computation_version=COMPUTATION_VERSION)
            try:
                return self.graph_store.write_entity_enrichment(
                    domain=DOMAIN,
                    entity_type=ENTITY_TYPE,
                    entity_id=str(supplier_id),
                    namespace=NAMESPACE,
                    metrics=metrics_for_dry_run,
                    computed_from=source_set,
                    dry_run=True,
                    idempotency_key=f"dry-run:{supplier_id}",
                )
            except NotImplementedError:
                pass
            except Exception as exc:
                receipt = _unsupported_receipt(supplier_id, dry_run=True)
                receipt.warnings.append(f"dry-run validation failed: {exc}")
                return receipt
        return EntityEnrichmentReceipt(
            domain=DOMAIN,
            entity_type=ENTITY_TYPE,
            entity_id=str(supplier_id),
            namespace=NAMESPACE,
            persisted=False,
            dry_run=True,
            metrics_written=sorted(metrics),
            metrics_rejected=[],
            protected_fields_rejected=[],
            idempotency_key=f"dry-run:{supplier_id}",
            computed_at=_now(),
            warnings=[],
        )

    def _verified_decisions(self) -> list[dict[str, Any]]:
        rows = self.reader.get_verified_decisions()
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _all_decisions(self) -> list[dict[str, Any]]:
        rows = self.reader.get_all_decisions()
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _supplier_ids(
        self,
        verified_decisions: list[dict[str, Any]],
        all_decisions: list[dict[str, Any]],
    ) -> list[str]:
        ids = {
            supplier_id
            for supplier_id in (_supplier_id(row) for row in verified_decisions + all_decisions)
            if supplier_id
        }
        ids.update(
            str(supplier.get("supplier_id"))
            for supplier in self.suppliers
            if supplier.get("supplier_id")
        )
        return sorted(ids)

    def _lead_time_metric(self, supplier_id: str) -> ProvenancedValue:
        result = compute_lead_time_result(self.invoices, supplier_id=supplier_id)
        sample_count = sum(stat.sample_count for stat in result.stats)
        if sample_count <= 0:
            return ProvenancedValue.unavailable("lead time unavailable")
        avg = sum(stat.actual_mean_days * stat.sample_count for stat in result.stats) / sample_count
        return ProvenancedValue.from_fixture(
            round(avg, 2),
            label=f"fixture lead-time context · integration pending ({sample_count} samples)",
        )

    def _otif_metric(self, supplier_id: str) -> ProvenancedValue:
        supplier = _supplier_fixture(self.suppliers, supplier_id)
        value = supplier.get("otif_score") if supplier else None
        if value is None:
            return ProvenancedValue.unavailable("OTIF unavailable")
        return ProvenancedValue.from_fixture(value, label="fixture OTIF context · integration pending")

    @staticmethod
    def serialize_values(metrics: dict[str, ProvenancedValue]) -> dict[str, dict[str, Any]]:
        return {
            name: serialize_provenanced_value(value)
            for name, value in sorted(metrics.items())
        }

    @staticmethod
    def serialize_receipt(receipt: EntityEnrichmentReceipt) -> dict[str, Any]:
        return asdict(receipt)


def serialize_provenanced_value(value: ProvenancedValue) -> dict[str, Any]:
    return {
        "value": value.value,
        "source": value.source,
        "provenance": _api_provenance(value),
        "provenance_tier": value.provenance_tier,
        "source_count": value.source_count,
        "factor_eligible": value.factor_eligible,
        "provenance_label": value.provenance_label,
        "measured": value.measured,
        "verified": value.verified,
        "computed_at": value.computed_at,
        "warnings": list(value.warnings),
    }


def _api_provenance(value: ProvenancedValue) -> str:
    """API provenance contract for fixture/live metric consumers."""
    if value.source == "fixture":
        return "sample"
    if value.provenance_tier == "scraped_external":
        return "scraped_external"
    if value.source == "verified_outcomes":
        return "real"
    return cast(str, value.provenance_tier)


def _verified_metric(value: Any, source_count: int, min_decisions: int, label: str) -> ProvenancedValue:
    return ProvenancedValue.from_verified(
        value,
        source_count=source_count,
        n_min=min_decisions,
        label=f"{label} ({source_count} verified)",
    )


def _unavailable_metric(label: str, warning: str | None = None) -> ProvenancedValue:
    metric = ProvenancedValue.unavailable(label)
    if warning:
        metric.warnings.append(warning)
    return metric


def _rate_metric(value: float, source_count: int, min_decisions: int, unavailable_label: str) -> ProvenancedValue:
    if source_count <= 0:
        return _unavailable_metric(
            unavailable_label,
            "rate metric requires at least one verified outcome",
        )
    return _verified_metric(value, source_count, min_decisions, "computed from verified S2P outcomes")


def _quarterly_metric(value: dict[str, int], source_count: int, min_decisions: int) -> ProvenancedValue:
    if source_count <= 0:
        metric = _unavailable_metric(
            "quarterly distribution unavailable · no verified outcomes",
            "quarterly distribution requires at least one verified outcome",
        )
        return ProvenancedValue(
            value={},
            source=metric.source,
            provenance_tier=metric.provenance_tier,
            source_count=metric.source_count,
            factor_eligible=metric.factor_eligible,
            provenance_label=metric.provenance_label,
            measured=metric.measured,
            verified=metric.verified,
            computed_at=metric.computed_at,
            warnings=list(metric.warnings),
        )
    return _verified_metric(value, source_count, min_decisions, "computed from verified S2P outcomes")


def _trend_metric(decisions: list[dict[str, Any]], min_decisions: int) -> ProvenancedValue:
    source_count = len(decisions)
    threshold = max(TREND_MIN_DECISIONS, min_decisions)
    if source_count < threshold:
        return ProvenancedValue(
            value="insufficient_data",
            source="unavailable",
            provenance_tier="unavailable",
            source_count=source_count,
            factor_eligible=False,
            provenance_label="trend unavailable · insufficient verified outcomes",
            measured=False,
            verified=False,
            computed_at=_now(),
            warnings=[f"trend requires at least {threshold} verified outcomes"],
        )
    return _verified_metric(_trend(decisions), source_count, threshold, "computed from verified S2P outcomes")


def _exception_rate(decisions: list[dict[str, Any]]) -> float:
    if not decisions:
        return 0.0
    exceptions = sum(1 for decision in decisions if not _is_correct(decision))
    return round(exceptions / len(decisions), 4)


def _accuracy(decisions: list[dict[str, Any]]) -> float:
    if not decisions:
        return 0.0
    correct = sum(1 for decision in decisions if _is_correct(decision))
    return round(correct / len(decisions), 4)


def _category_distribution(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(decision.get("category") or "unknown") for decision in decisions)
    return dict(sorted(counts.items()))


def _top_category(decisions: list[dict[str, Any]]) -> str | None:
    distribution = _category_distribution(decisions)
    if not distribution:
        return None
    return sorted(distribution.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _decision_count_by_quarter(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for decision in decisions:
        parsed = _decision_datetime(decision)
        if parsed is None:
            continue
        counts[f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"] += 1
    return dict(sorted(counts.items()))


def _trend(decisions: list[dict[str, Any]]) -> str:
    dated = [
        (parsed, 0 if _is_correct(decision) else 1)
        for decision in decisions
        if (parsed := _decision_datetime(decision)) is not None
    ]
    if len(dated) < TREND_MIN_DECISIONS:
        return "insufficient_data"
    dated.sort(key=lambda item: item[0])
    midpoint = len(dated) // 2
    earlier = dated[:midpoint]
    recent = dated[midpoint:]
    if not earlier or not recent:
        return "insufficient_data"
    earlier_rate = sum(value for _, value in earlier) / len(earlier)
    recent_rate = sum(value for _, value in recent) / len(recent)
    if recent_rate > earlier_rate + 0.10:
        return "deteriorating"
    if recent_rate < earlier_rate - 0.10:
        return "improving"
    return "stable"


def _decision_datetime(decision: dict[str, Any]) -> datetime | None:
    for key in ("verified_at", "created_at", "timestamp"):
        value = decision.get(key)
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    metadata = decision.get("metadata")
    if isinstance(metadata, dict):
        for key in ("invoice_date", "created_at", "timestamp"):
            parsed = _parse_datetime(metadata.get(key))
            if parsed is not None:
                return parsed
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text[:10])
        except ValueError:
            return None


def _supplier_id(decision: dict[str, Any]) -> str | None:
    for source in (decision.get("metadata"), decision.get("context"), decision):
        if isinstance(source, dict):
            value = source.get("supplier_id") or source.get("supplier")
            if value not in (None, ""):
                return str(value)
    return None


def _decision_id(decision: dict[str, Any]) -> str | None:
    value = decision.get("decision_id")
    return str(value) if value not in (None, "") else None


def _is_correct(decision: dict[str, Any]) -> bool:
    return bool(decision.get("is_correct", False))


def _supplier_fixture(suppliers: list[dict[str, Any]], supplier_id: str) -> dict[str, Any] | None:
    for supplier in suppliers:
        if str(supplier.get("supplier_id") or "") == str(supplier_id):
            return supplier
    return None


def _unsupported_receipt(supplier_id: str, *, dry_run: bool) -> EntityEnrichmentReceipt:
    return EntityEnrichmentReceipt(
        domain=DOMAIN,
        entity_type=ENTITY_TYPE,
        entity_id=str(supplier_id),
        namespace=NAMESPACE,
        persisted=False,
        dry_run=dry_run,
        metrics_written=[],
        metrics_rejected=[],
        protected_fields_rejected=[],
        idempotency_key="",
        computed_at=_now(),
        warnings=["GraphStore entity enrichment write API unavailable"],
    )


def _idempotency_key(supplier_id: str, source_set: EnrichmentSourceSet) -> str:
    return f"{COMPUTATION_VERSION}:{supplier_id}:{source_set.verified_decision_count}:{source_set.unverified_decision_count}"


def _numeric_value(payload: Any) -> float:
    if isinstance(payload, dict):
        payload = payload.get("value")
    try:
        return float(payload)
    except (TypeError, ValueError):
        return 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DOMAIN",
    "ENTITY_TYPE",
    "NAMESPACE",
    "S2PSupplierEnrichmentService",
    "serialize_provenanced_value",
]
