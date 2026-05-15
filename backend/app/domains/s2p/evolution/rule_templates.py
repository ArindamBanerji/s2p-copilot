from __future__ import annotations

from copy import deepcopy
from itertools import islice, permutations
from typing import Any, Protocol

from app.domains.s2p.config import S2PDomainConfig
from app.framework.composite_gate import CompositeDiscriminant


class RuleTemplate(Protocol):
    name: str
    success_metric_name: str
    applicable_categories: tuple[str, ...]

    def generate_variants(self) -> list[dict[str, Any]]:
        ...

    def evaluate_batch(self, variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        ...


def _clamp(value: float, low: float, high: float) -> float:
    return round(max(low, min(high, float(value))), 3)


def _category(decision: dict[str, Any]) -> str:
    return str(decision.get("category") or "")


def _amount(decision: dict[str, Any]) -> float:
    for key in ("amount", "total_amount", "invoice_amount"):
        if key in decision:
            try:
                return float(decision[key] or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _confidence(decision: dict[str, Any]) -> float:
    try:
        return float(decision.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _recommended(decision: dict[str, Any]) -> str:
    return str(decision.get("recommended_action") or decision.get("action") or "")


def _actual(decision: dict[str, Any]) -> str:
    return str(
        decision.get("ground_truth_action")
        or decision.get("actual_action")
        or decision.get("analyst_action")
        or ""
    )


def _success_rate(matches: list[bool]) -> float:
    if not matches:
        return 0.0
    return sum(1 for item in matches if item) / len(matches)


def _result(metric_name: str, metric: float, baseline: float, sample_size: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "metric_name": metric_name,
        "metric": round(float(metric), 4),
        "baseline_metric": round(float(baseline), 4),
        "sample_size": int(sample_size),
    }
    if extra:
        payload.update(deepcopy(extra))
    return payload


class AutoApproveThresholdRule:
    name = "auto_approve_threshold_sweep"
    success_metric_name = "safe_auto_approve_rate"
    applicable_categories = tuple(S2PDomainConfig.categories)

    def generate_variants(self) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = []
        for category in self.applicable_categories:
            base = float(
                CompositeDiscriminant.CATEGORY_CONFIDENCE_THRESHOLDS.get(
                    category,
                    CompositeDiscriminant.CONFIDENCE_THRESHOLD,
                )
            )
            for delta in (-0.06, -0.03, 0.03, 0.06):
                threshold = _clamp(base + delta, 0.70, 0.97)
                variants.append(
                    {
                        "variant_id": f"{self.name}:{category}:{threshold:.2f}",
                        "template_name": self.name,
                        "category": category,
                        "threshold": threshold,
                        "baseline_threshold": round(base, 3),
                        "parameter": "confidence_threshold",
                    }
                )
        return variants

    def evaluate_batch(self, variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        category = str(variant.get("category") or "")
        threshold = float(variant.get("threshold", 1.0))
        baseline_threshold = float(variant.get("baseline_threshold", threshold))
        scoped = [deepcopy(item) for item in decisions if _category(item) == category]

        def safe_rate(limit: float) -> float:
            approved = [item for item in scoped if _confidence(item) >= limit and _recommended(item) == "auto_approve"]
            return _success_rate([_actual(item) in ("", "auto_approve") for item in approved])

        return _result(
            self.success_metric_name,
            safe_rate(threshold),
            safe_rate(baseline_threshold),
            len(scoped),
            {"category": category, "threshold": threshold},
        )


class RoutingPriorityRule:
    name = "routing_priority_permutation"
    success_metric_name = "routing_precision"
    applicable_categories = tuple(S2PDomainConfig.categories)

    def generate_variants(self) -> list[dict[str, Any]]:
        preferred = ["auto_approve", "hold_for_review", "escalate_to_buyer", "flag_leakage", "refer_to_specialist"]
        variants = []
        for index, ordering in enumerate(islice(permutations(preferred, len(preferred)), 4), start=1):
            variants.append(
                {
                    "variant_id": f"{self.name}:order:{index}",
                    "template_name": self.name,
                    "categories": list(self.applicable_categories),
                    "action_priority": list(ordering),
                    "parameter": "action_priority",
                }
            )
        return variants

    def evaluate_batch(self, variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        priority = list(variant.get("action_priority") or S2PDomainConfig.actions)
        scoped = [deepcopy(item) for item in decisions if _category(item) in self.applicable_categories]

        variant_matches = []
        baseline_matches = []
        for item in scoped:
            allowed = list(item.get("acceptable_actions") or [])
            selected = next((action for action in priority if action in allowed), _recommended(item))
            actual = _actual(item)
            variant_matches.append(selected == actual)
            baseline_matches.append(_recommended(item) == actual)

        return _result(
            self.success_metric_name,
            _success_rate(variant_matches),
            _success_rate(baseline_matches),
            len(scoped),
            {"action_priority": priority},
        )


class EscalationTriggerAmountRule:
    name = "escalation_trigger_amount"
    success_metric_name = "high_value_capture_rate"
    applicable_categories = ("price_variance", "contract_gap", "quantity_mismatch")

    def generate_variants(self) -> list[dict[str, Any]]:
        return [
            {
                "variant_id": f"{self.name}:{amount}",
                "template_name": self.name,
                "categories": list(self.applicable_categories),
                "amount_threshold": amount,
                "parameter": "amount_threshold",
            }
            for amount in (25000, 50000, 75000)
        ]

    def evaluate_batch(self, variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        threshold = float(variant.get("amount_threshold", 50000))
        scoped = [deepcopy(item) for item in decisions if _category(item) in self.applicable_categories]
        high_value = [item for item in scoped if _amount(item) >= threshold]
        variant_matches = [_actual(item) in ("escalate_to_buyer", "flag_leakage") for item in high_value]
        baseline_matches = [_recommended(item) == _actual(item) for item in high_value]
        return _result(
            self.success_metric_name,
            _success_rate(variant_matches),
            _success_rate(baseline_matches),
            len(high_value),
            {"amount_threshold": threshold},
        )


class SupplierFlagSensitivityRule:
    name = "supplier_flag_sensitivity"
    success_metric_name = "supplier_exception_precision"
    applicable_categories = ("duplicate_risk", "contract_gap", "price_variance")

    def generate_variants(self) -> list[dict[str, Any]]:
        levels = [("low", 2), ("medium", 3), ("high", 5)]
        return [
            {
                "variant_id": f"{self.name}:{name}",
                "template_name": self.name,
                "categories": list(self.applicable_categories),
                "sensitivity": name,
                "repeat_threshold": threshold,
                "parameter": "repeat_threshold",
            }
            for name, threshold in levels
        ]

    def evaluate_batch(self, variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        repeat_threshold = int(variant.get("repeat_threshold", 3))
        scoped = [deepcopy(item) for item in decisions if _category(item) in self.applicable_categories]
        supplier_counts: dict[str, int] = {}
        for item in scoped:
            supplier = str(item.get("supplier_id") or "unknown")
            supplier_counts[supplier] = supplier_counts.get(supplier, 0) + 1
        flagged = [
            item
            for item in scoped
            if supplier_counts.get(str(item.get("supplier_id") or "unknown"), 0) >= repeat_threshold
        ]
        variant_matches = [_actual(item) in ("flag_leakage", "escalate_to_buyer") for item in flagged]
        baseline_matches = [_recommended(item) == _actual(item) for item in flagged]
        return _result(
            self.success_metric_name,
            _success_rate(variant_matches),
            _success_rate(baseline_matches),
            len(flagged),
            {"repeat_threshold": repeat_threshold, "flagged_suppliers": sorted(supplier_counts)},
        )


class EvidencePresentationOrderRule:
    name = "evidence_presentation_order"
    success_metric_name = "analyst_confirmation_rate"
    applicable_categories = tuple(S2PDomainConfig.categories)

    def generate_variants(self) -> list[dict[str, Any]]:
        orders = {
            "category_weighted": ["factor_fingerprint", "similar_invoices", "audit_trail"],
            "supplier_first": ["supplier_history", "contract_terms", "factor_fingerprint"],
            "recency_first": ["recent_activity", "factor_fingerprint", "audit_trail"],
        }
        return [
            {
                "variant_id": f"{self.name}:{name}",
                "template_name": self.name,
                "categories": list(self.applicable_categories),
                "order_strategy": name,
                "panel_order": list(order),
                "parameter": "panel_order",
            }
            for name, order in orders.items()
        ]

    def evaluate_batch(self, variant: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        strategy = str(variant.get("order_strategy") or "")
        scoped = [deepcopy(item) for item in decisions if _category(item) in self.applicable_categories]
        variant_matches = [
            bool(item.get("analyst_confirmed", _recommended(item) == _actual(item)))
            or strategy == str(item.get("preferred_evidence_order") or "")
            for item in scoped
        ]
        baseline_matches = [bool(item.get("analyst_confirmed", _recommended(item) == _actual(item))) for item in scoped]
        return _result(
            self.success_metric_name,
            _success_rate(variant_matches),
            _success_rate(baseline_matches),
            len(scoped),
            {"order_strategy": strategy, "panel_order": list(variant.get("panel_order") or [])},
        )


def get_s2p_rules() -> list[RuleTemplate]:
    return [
        AutoApproveThresholdRule(),
        RoutingPriorityRule(),
        EscalationTriggerAmountRule(),
        SupplierFlagSensitivityRule(),
        EvidencePresentationOrderRule(),
    ]
