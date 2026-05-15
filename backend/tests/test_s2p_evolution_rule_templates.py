from copy import deepcopy

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.evolution.rule_templates import (
    AutoApproveThresholdRule,
    EscalationTriggerAmountRule,
    EvidencePresentationOrderRule,
    RoutingPriorityRule,
    SupplierFlagSensitivityRule,
    get_s2p_rules,
)


def _decisions():
    return [
        {
            "category": "price_variance",
            "supplier_id": "SUP-1",
            "recommended_action": "auto_approve",
            "ground_truth_action": "auto_approve",
            "confidence": 0.94,
            "amount": 62000,
            "acceptable_actions": ["auto_approve", "hold_for_review"],
            "analyst_confirmed": True,
            "preferred_evidence_order": "category_weighted",
        },
        {
            "category": "price_variance",
            "supplier_id": "SUP-1",
            "recommended_action": "auto_approve",
            "ground_truth_action": "hold_for_review",
            "confidence": 0.80,
            "amount": 28000,
            "acceptable_actions": ["hold_for_review", "escalate_to_buyer"],
            "analyst_confirmed": False,
        },
        {
            "category": "contract_gap",
            "supplier_id": "SUP-1",
            "recommended_action": "hold_for_review",
            "ground_truth_action": "escalate_to_buyer",
            "confidence": 0.76,
            "total_amount": 99000,
            "acceptable_actions": ["escalate_to_buyer", "flag_leakage"],
            "analyst_confirmed": True,
        },
        {
            "category": "duplicate_risk",
            "supplier_id": "SUP-2",
            "recommended_action": "hold_for_review",
            "ground_truth_action": "flag_leakage",
            "confidence": 0.88,
            "amount": 12000,
            "acceptable_actions": ["flag_leakage"],
            "analyst_confirmed": False,
        },
    ]


def test_rule_template_protocol_fields():
    rules = get_s2p_rules()

    assert len(rules) == 5
    for rule in rules:
        assert rule.name
        assert rule.success_metric_name
        assert set(rule.applicable_categories).issubset(set(S2PDomainConfig.categories))
        assert rule.generate_variants()


def test_auto_approve_threshold_generates_clamped_variants():
    variants = AutoApproveThresholdRule().generate_variants()

    assert variants
    assert all(0.70 <= variant["threshold"] <= 0.97 for variant in variants)
    assert {"price_variance", "duplicate_risk"}.issubset({variant["category"] for variant in variants})


def test_auto_approve_threshold_evaluates_safe_auto_approve_rate():
    rule = AutoApproveThresholdRule()
    variant = {
        "variant_id": "v1",
        "category": "price_variance",
        "threshold": 0.91,
        "baseline_threshold": 0.79,
    }

    result = rule.evaluate_batch(variant, _decisions())

    assert result["metric_name"] == "safe_auto_approve_rate"
    assert result["metric"] > result["baseline_metric"]
    assert result["sample_size"] == 2


def test_routing_priority_generates_deterministic_variants():
    rule = RoutingPriorityRule()

    assert rule.generate_variants() == rule.generate_variants()
    assert len(rule.generate_variants()) == 4


def test_escalation_trigger_amount_marks_high_value_capture():
    rule = EscalationTriggerAmountRule()
    variant = rule.generate_variants()[1]

    result = rule.evaluate_batch(variant, _decisions())

    assert result["metric_name"] == "high_value_capture_rate"
    assert result["sample_size"] >= 1
    assert result["metric"] >= result["baseline_metric"]


def test_supplier_flag_sensitivity_groups_by_supplier():
    rule = SupplierFlagSensitivityRule()
    variant = {"variant_id": "supplier", "repeat_threshold": 2}

    result = rule.evaluate_batch(variant, _decisions())

    assert result["metric_name"] == "supplier_exception_precision"
    assert result["sample_size"] == 3
    assert "SUP-1" in result["flagged_suppliers"]


def test_evidence_presentation_order_generates_category_specific_orders():
    rule = EvidencePresentationOrderRule()
    variants = rule.generate_variants()

    assert {variant["order_strategy"] for variant in variants} == {
        "category_weighted",
        "supplier_first",
        "recency_first",
    }
    assert all(variant["panel_order"] for variant in variants)


def test_rule_templates_do_not_mutate_input_decisions():
    rule = RoutingPriorityRule()
    variant = rule.generate_variants()[0]
    decisions = _decisions()
    before = deepcopy(decisions)

    rule.evaluate_batch(variant, decisions)

    assert decisions == before
