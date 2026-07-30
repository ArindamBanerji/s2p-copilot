"""Read-only supplier intelligence profile composition for R18A.

This module consumes existing P39B enrichment and fixture/accumulator context.
It does not compute or persist new enrichment.
"""

from __future__ import annotations

from typing import Any

from copilot_sdk.graph.enrichment import ProvenancedValue
from copilot_sdk.graph.protocol import GraphStore

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import S2PGraphReader
from app.routers.s2p_data_helpers import load_invoices, load_suppliers
from app.services.s2p_enrichment import (
    DOMAIN as S2P_ENRICHMENT_DOMAIN,
    ENTITY_TYPE as S2P_ENRICHMENT_ENTITY_TYPE,
    NAMESPACE as S2P_ENRICHMENT_NAMESPACE,
    serialize_provenanced_value,
)
from app.services.supplier_profile_accumulator import accumulator as default_accumulator


DEPTH_THRESHOLDS = {
    "emerging": 1,
    "developing": 20,
    "reliable": 50,
    "deep": 100,
}
RISK_MIN_VERIFIED_SAMPLE = 20
RISK_BEARING_METRICS = {
    "exception_rate",
    "accuracy",
    "risk_score",
    "supplier_risk_score",
}
NEGATIVE_TREND_VALUES = {
    "deteriorating",
    "declining",
    "worsening",
    "degraded",
    "increasing_exception_rate",
    "rising_exception_rate",
    "negative",
}
DISCREPANCY_ACTIONS = {
    "flag_leakage": "flag_leakage",
    "hold_for_review": "hold_for_review",
    # The actual S2P taxonomy has escalate_to_buyer, not the design-doc example
    # escalate_compliance. Treat it as the safe S2P escalation discrepancy class.
    "escalate_to_buyer": "escalation",
}


class SupplierIntelligenceComposer:
    """Compose display/API supplier intelligence from existing read models."""

    def __init__(
        self,
        *,
        graph_store: Any | None = None,
        reader: S2PGraphReader | None = None,
        accumulator: Any = default_accumulator,
        suppliers: list[dict[str, Any]] | None = None,
        invoices: list[dict[str, Any]] | None = None,
        weekly_decision_rate: float = 5.0,
    ) -> None:
        self.graph_store = graph_store
        self.reader = reader or (S2PGraphReader(store=graph_store) if graph_store is not None else None)
        self.accumulator = accumulator
        self.suppliers = suppliers if suppliers is not None else load_suppliers()
        self.invoices = invoices if invoices is not None else load_invoices()
        self.weekly_decision_rate = weekly_decision_rate

    def compose_profile(self, supplier_id: str) -> dict[str, Any]:
        enrichment = self._read_enrichment(supplier_id)
        fixture_context = self._supplier_fixture(supplier_id)
        warnings: list[str] = []
        if not enrichment:
            warnings.append("p39b_enrichment_unavailable")

        depth = self.intelligence_depth(enrichment)
        risk = self.risk_tier(enrichment)
        caught = self.caught_discrepancies(
            self._verified_decisions_for_supplier(supplier_id),
            supplier_context={"supplier_id": supplier_id},
        )
        behavioral_metrics = self._behavioral_metrics(supplier_id, enrichment, fixture_context)
        exposure = self.economic_exposure(enrichment, fixture_context)
        summary_name = (
            (fixture_context or {}).get("name")
            or (fixture_context or {}).get("supplier_name")
            or supplier_id
        )
        warnings.extend(behavioral_metrics.get("warnings", []))
        warnings.extend(risk.get("warnings", []))
        warnings.extend(caught.get("warnings", []))
        if exposure and exposure.get("warnings"):
            warnings.extend(exposure["warnings"])

        return {
            "depth": depth,
            "risk": risk,
            "caught": caught,
            "behavioral_metrics": {
                key: value
                for key, value in behavioral_metrics.items()
                if key != "warnings"
            },
            "economic_exposure": exposure,
            "new_manager_summary": self.new_manager_summary(summary_name, depth, risk, caught),
            "warnings": sorted(set(warnings)),
        }

    def intelligence_depth(self, enrichment: dict[str, Any], domain: str = "s2p") -> dict[str, Any]:
        del domain
        per_metric = {
            name: self._metric_depth(self._metric_source_count_for_depth(name, metric))
            for name, metric in sorted(enrichment.items())
            if self._is_verified_metric(name, metric)
        }
        total = len(per_metric)
        past_threshold = sum(
            1 for detail in per_metric.values() if detail["tier"] in {"reliable", "deep"}
        )
        has_deep = any(detail["tier"] == "deep" for detail in per_metric.values())
        if total == 0:
            headline = "none"
        elif past_threshold == 0:
            headline = "emerging"
        elif past_threshold * 2 < total:
            headline = "developing"
        elif past_threshold == total and has_deep:
            headline = "deep"
        elif past_threshold == total:
            headline = "comprehensive"
        else:
            headline = "reliable"

        current_count = max(
            [detail["source_count"] for detail in per_metric.values()] or [0]
        )
        return {
            "headline_tier": headline,
            "metrics_past_threshold": past_threshold,
            "metrics_total": total,
            "label": f"{past_threshold} of {total} metrics past threshold",
            "per_metric": per_metric,
            "trajectory": self.trajectory_projection(
                current_count,
                self.weekly_decision_rate,
                {"reliable": DEPTH_THRESHOLDS["reliable"], "deep": DEPTH_THRESHOLDS["deep"]},
            ),
        }

    def risk_tier(self, enrichment: dict[str, Any]) -> dict[str, Any]:
        if not enrichment:
            return {
                "tier": "integration_pending",
                "basis": "integration_pending",
                "source_count": 0,
                "reason": "P39B enrichment is not available for this supplier.",
                "warnings": ["risk_requires_p39b_verified_enrichment"],
            }

        learned = {
            name: metric
            for name, metric in enrichment.items()
            if self._is_learned_verified(metric)
        }
        context = {
            name: metric
            for name, metric in enrichment.items()
            if isinstance(metric, ProvenancedValue) and not self._is_learned_verified(metric)
        }
        if not learned:
            return {
                "tier": "monitor" if context else "integration_pending",
                "basis": "context" if context else "integration_pending",
                "source_count": 0,
                "reason": "Only context or fixture signals are available; learned risk tier is withheld.",
                "warnings": ["context_only_risk_cannot_be_high_medium_or_low"],
            }

        risk_metrics = {
            name: metric
            for name, metric in learned.items()
            if self._is_risk_bearing_metric(name, metric)
        }
        if not risk_metrics:
            source_count = max(self._source_count(metric) for metric in learned.values())
            return {
                "tier": "insufficient_data",
                "basis": "insufficient_data",
                "source_count": source_count,
                "reason": "Learned enrichment exists, but no learned risk-bearing metric is available.",
                "warnings": ["learned_risk_bearing_metric_unavailable"],
            }

        source_count = max(self._source_count(metric) for metric in risk_metrics.values())
        if 0 < source_count < RISK_MIN_VERIFIED_SAMPLE:
            return {
                "tier": "insufficient_data",
                "basis": "insufficient_data",
                "source_count": source_count,
                "reason": f"Verified sample is below {RISK_MIN_VERIFIED_SAMPLE}.",
                "warnings": ["verified_sample_below_risk_threshold"],
            }

        exception_rate = self._metric_number(risk_metrics.get("exception_rate"))
        accuracy = self._metric_number(risk_metrics.get("accuracy"))
        trend = self._metric_value(risk_metrics.get("trend"))
        if (
            (exception_rate is not None and exception_rate >= 0.12)
            or (accuracy is not None and accuracy < 0.80)
            or str(trend).lower() in {"deteriorating", "declining"}
        ):
            tier = "high"
        elif (
            (exception_rate is not None and exception_rate <= 0.05)
            and (accuracy is None or accuracy >= 0.90)
        ):
            tier = "low"
        else:
            tier = "medium"
        return {
            "tier": tier,
            "basis": "learned",
            "source_count": source_count,
            "reason": "Risk tier is based on learned verified supplier enrichment.",
            "contributing_metrics": sorted(risk_metrics),
            "warnings": [],
        }

    def caught_discrepancies(
        self,
        enrichment_or_decisions: Any,
        supplier_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        decisions = enrichment_or_decisions if isinstance(enrichment_or_decisions, list) else []
        supplier_id = str((supplier_context or {}).get("supplier_id") or "")
        caught: list[dict[str, Any]] = []
        for decision in decisions:
            if supplier_id and self._supplier_id(decision) != supplier_id:
                continue
            action = self._decision_action(decision)
            if action not in DISCREPANCY_ACTIONS:
                continue
            if not self._decision_correct(decision):
                continue
            caught.append(decision)
        amount = sum(self._decision_amount(decision) for decision in caught)
        warnings = []
        if not decisions:
            warnings.append("caught_discrepancies_require_verified_decisions")
        return {
            "count": len(caught),
            "action_mapping": dict(DISCREPANCY_ACTIONS),
            "source": "verified_outcomes" if caught else "unavailable",
            "verified_decision_count": len(decisions),
            "flagged_invoice_value": round(amount, 2),
            "currency": "USD",
            "warnings": warnings,
        }

    def economic_exposure(
        self,
        enrichment: dict[str, Any],
        fixture_context: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        exception_metric = enrichment.get("exception_rate")
        verified_count_metric = enrichment.get("verified_decisions")
        if not self._is_learned_verified(exception_metric):
            return None
        exception_rate = self._metric_number(exception_metric)
        verified_count = self._metric_number(verified_count_metric)
        avg_invoice = self._safe_float((fixture_context or {}).get("avg_invoice_amount"))
        if exception_rate is None or verified_count is None or avg_invoice is None:
            return None
        amount = round(exception_rate * verified_count * avg_invoice, 2)
        return {
            "amount": amount,
            "currency": "USD",
            "computation": "exception_rate * verified_decisions * fixture_avg_invoice_amount",
            "source_breakdown": {
                "exception_rate": self._metric_source_summary(exception_metric),
                "verified_decisions": self._metric_source_summary(verified_count_metric),
                "avg_invoice_amount": {
                    "source": "fixture",
                    "provenance_tier": "context",
                    "measured": False,
                    "verified": False,
                },
            },
            "caveat": (
                "Mixed-source exception exposure estimate only; not confirmed savings, "
                "recovered dollars, ROI, annual savings, or confirmed leakage."
            ),
            "warnings": ["economic_exposure_is_not_confirmed_savings"],
        }

    def trajectory_projection(
        self,
        current_count: int,
        weekly_decision_rate: float,
        thresholds: dict[str, int],
    ) -> dict[str, Any]:
        projections = {}
        for name, threshold in sorted(thresholds.items(), key=lambda item: item[1]):
            remaining = max(0, int(threshold) - int(current_count))
            weeks = None if weekly_decision_rate <= 0 else round(remaining / weekly_decision_rate, 2)
            projections[name] = {
                "threshold": threshold,
                "remaining_decisions": remaining,
                "estimated_weeks": weeks,
            }
        return {
            "current_count": int(current_count),
            "weekly_decision_rate": weekly_decision_rate,
            "thresholds": projections,
        }

    def new_manager_summary(
        self,
        supplier_name: str,
        depth: dict[str, Any],
        risk: dict[str, Any],
        caught: dict[str, Any],
    ) -> str:
        return (
            f"{supplier_name}: intelligence depth is {depth.get('headline_tier')} "
            f"({depth.get('label')}); risk is {risk.get('tier')} based on "
            f"{risk.get('basis')}. Caught discrepancies: {caught.get('count', 0)}."
        )

    def resolve_metric(self, metric_name: str, supplier_id: str) -> dict[str, Any]:
        enrichment = self._read_enrichment(supplier_id)
        metric = enrichment.get(metric_name)
        if isinstance(metric, ProvenancedValue):
            return self._serialize_metric(metric)
        profile = self.accumulator.get_profile(supplier_id) if self.accumulator else None
        if profile is not None and hasattr(profile, metric_name):
            value = getattr(profile, metric_name)
            if value is not None:
                return self._serialize_metric(
                    ProvenancedValue.from_fixture(
                        value,
                        label="computed from decision history (not outcome-verified)",
                    )
                )
        fixture = self._supplier_fixture(supplier_id) or {}
        if metric_name in fixture:
            return self._serialize_metric(
                ProvenancedValue.from_fixture(
                    fixture[metric_name],
                    label="fixture/context only; connect systems to verify",
                )
            )
        return self._serialize_metric(ProvenancedValue.unavailable(f"{metric_name} unavailable"))

    def _read_enrichment(self, supplier_id: str) -> dict[str, Any]:
        if not isinstance(self.graph_store, GraphStore):
            return {}
        try:
            value = self.graph_store.read_entity_enrichment(
                domain=S2P_ENRICHMENT_DOMAIN,
                entity_type=S2P_ENRICHMENT_ENTITY_TYPE,
                entity_id=str(supplier_id),
                namespace=S2P_ENRICHMENT_NAMESPACE,
            )
        except (AttributeError, NotImplementedError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _verified_decisions_for_supplier(self, supplier_id: str) -> list[dict[str, Any]]:
        if self.reader is None:
            return []
        decisions = self.reader.get_verified_decisions()
        if not isinstance(decisions, list):
            return []
        return [
            decision for decision in decisions
            if isinstance(decision, dict) and self._supplier_id(decision) == str(supplier_id)
        ]

    def _behavioral_metrics(
        self,
        supplier_id: str,
        enrichment: dict[str, Any],
        fixture_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        learned: dict[str, Any] = {}
        context: dict[str, Any] = {}
        unavailable: dict[str, Any] = {}
        warnings: list[str] = []
        for name, metric in sorted(enrichment.items()):
            if not isinstance(metric, ProvenancedValue):
                continue
            serialized = self._serialize_metric(metric)
            if self._is_learned_verified(metric):
                learned[name] = serialized
            elif metric.source == "unavailable":
                unavailable[name] = serialized
            else:
                context[name] = serialized

        profile = self.accumulator.get_profile(supplier_id) if self.accumulator else None
        if profile is not None:
            for name in ("exception_rate", "otif", "avg_lead_time_days", "invoice_count"):
                if name in learned or name in context or name in unavailable:
                    continue
                value = getattr(profile, name, None)
                if value is not None:
                    context[name] = self._serialize_metric(
                        ProvenancedValue.from_fixture(
                            value,
                            label="computed from decision history (not outcome-verified)",
                        )
                    )

        if fixture_context:
            for name in ("payment_terms", "avg_invoice_amount", "otif_score"):
                if name in learned or name in context or name in unavailable:
                    continue
                value = fixture_context.get(name)
                if value is not None:
                    context[name] = self._serialize_metric(
                        ProvenancedValue.from_fixture(
                            value,
                            label="fixture/context only; connect systems to verify",
                        )
                    )

        if not learned:
            warnings.append("no_learned_verified_behavioral_metrics")
        return {
            "learned": learned,
            "context": context,
            "unavailable": unavailable,
            "warnings": warnings,
        }

    def _metric_depth(self, source_count: int) -> dict[str, Any]:
        if source_count <= 0:
            tier = "none"
        elif source_count < DEPTH_THRESHOLDS["developing"]:
            tier = "emerging"
        elif source_count < DEPTH_THRESHOLDS["reliable"]:
            tier = "developing"
        elif source_count < DEPTH_THRESHOLDS["deep"]:
            tier = "reliable"
        else:
            tier = "deep"
        return {"tier": tier, "source_count": int(source_count)}

    def _supplier_fixture(self, supplier_id: str) -> dict[str, Any] | None:
        for supplier in self.suppliers:
            if str(supplier.get("supplier_id") or "") == str(supplier_id):
                return supplier
        return None

    def _serialize_metric(self, metric: ProvenancedValue) -> dict[str, Any]:
        return serialize_provenanced_value(metric)

    def _metric_source_summary(self, metric: Any) -> dict[str, Any]:
        if not isinstance(metric, ProvenancedValue):
            return {"source": "unknown", "provenance_tier": "unknown"}
        return {
            "source": metric.source,
            "provenance_tier": metric.provenance_tier,
            "source_count": metric.source_count,
            "measured": metric.measured,
            "verified": metric.verified,
        }

    def _source_count(self, metric: Any) -> int:
        return int(getattr(metric, "source_count", 0) or 0)

    def _is_verified_metric(self, name: str, metric: Any) -> bool:
        if not self._is_learned_verified(metric):
            return False
        if str(name).lower() in {"total_decisions", "unverified_decisions"}:
            return False
        return True

    def _metric_source_count_for_depth(self, name: str, metric: Any) -> int:
        if not self._is_verified_metric(name, metric):
            return 0
        return self._source_count(metric)

    def _is_risk_bearing_metric(self, name: str, metric: Any) -> bool:
        if not self._is_learned_verified(metric):
            return False
        lowered = str(name).lower()
        if lowered == "trend":
            return self._is_negative_trend(self._metric_value(metric))
        if lowered in RISK_BEARING_METRICS:
            return True
        return lowered.endswith("_risk") or "risk" in lowered

    def _is_negative_trend(self, value: Any) -> bool:
        return str(value or "").strip().lower() in NEGATIVE_TREND_VALUES

    def _metric_value(self, metric: Any) -> Any:
        return getattr(metric, "value", None)

    def _metric_number(self, metric: Any) -> float | None:
        return self._safe_float(self._metric_value(metric))

    def _is_learned_verified(self, metric: Any) -> bool:
        return (
            isinstance(metric, ProvenancedValue)
            and metric.source == "verified_outcomes"
            and metric.provenance_tier == "learned"
            and metric.measured
            and metric.verified
        )

    def _supplier_id(self, decision: dict[str, Any]) -> str | None:
        for source in (decision.get("metadata"), decision.get("context"), decision):
            if isinstance(source, dict):
                value = source.get("supplier_id") or source.get("supplier")
                if value not in (None, ""):
                    return str(value)
        return None

    def _decision_action(self, decision: dict[str, Any]) -> str | None:
        for source in (decision, decision.get("metadata"), decision.get("context")):
            if isinstance(source, dict):
                value = (
                    source.get("actual_action")
                    or source.get("recommended_action")
                    or source.get("action")
                    or source.get("ground_truth_action")
                )
                if value not in (None, ""):
                    return str(value)
        return None

    def _decision_correct(self, decision: dict[str, Any]) -> bool:
        for source in (decision, decision.get("metadata"), decision.get("context")):
            if isinstance(source, dict):
                if (
                    source.get("is_correct") is True
                    or source.get("correct") is True
                    or source.get("confirmed") is True
                    or source.get("verified_correct") is True
                ):
                    return True
                if str(source.get("outcome") or "").lower() in {"correct", "confirmed", "true_positive"}:
                    return True
        return False

    def _decision_amount(self, decision: dict[str, Any]) -> float:
        for source in (decision, decision.get("metadata"), decision.get("context")):
            if isinstance(source, dict):
                value = source.get("amount") or source.get("invoice_amount")
                number = self._safe_float(value)
                if number is not None:
                    return number
        return 0.0

    def _safe_float(self, value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
