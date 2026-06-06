"""S2P prompt/rule variant configuration."""

from __future__ import annotations

from copilot_sdk.evolution import PromptEvolverConfig, VariantSpec

from app.domains.s2p.config import S2PDomainConfig


S2P_VARIANTS: tuple[VariantSpec, ...] = (
    VariantSpec(
        id="EVIDENCE_ORDER_v1",
        family="evidence_ordering",
        version=1,
        status="active",
        metadata={
            "order": ["factor_fingerprint", "similar_invoices", "audit_trail"],
        },
    ),
    VariantSpec(
        id="EVIDENCE_ORDER_v2",
        family="evidence_ordering",
        version=2,
        status="shadow",
        metadata={
            "order": ["supplier_history", "contract_terms", "factor_fingerprint"],
        },
    ),
    VariantSpec(
        id="ROUTING_THRESHOLD_v1",
        family="routing_threshold",
        version=1,
        status="active",
        metadata={
            "auto_approve_confidence": 0.86,
            "escalate_confidence": 0.68,
        },
    ),
    VariantSpec(
        id="ROUTING_THRESHOLD_v2",
        family="routing_threshold",
        version=2,
        status="shadow",
        metadata={
            "auto_approve_confidence": 0.91,
            "escalate_confidence": 0.72,
        },
    ),
    VariantSpec(
        id="ESCALATION_CRITERIA_v1",
        family="escalation_criteria",
        version=1,
        status="active",
        metadata={
            "missing_po_escalate": True,
            "amount_threshold": 50000,
        },
    ),
    VariantSpec(
        id="ESCALATION_CRITERIA_v2",
        family="escalation_criteria",
        version=2,
        status="shadow",
        metadata={
            "missing_po_escalate": True,
            "amount_threshold": 25000,
        },
    ),
    VariantSpec(
        id="TRIAGE_WEIGHTS_v1",
        family="triage_weights",
        version=1,
        status="active",
        metadata={
            "amount_variance_weight": 0.4,
            "match_status_weight": 0.6,
        },
    ),
    VariantSpec(
        id="TRIAGE_WEIGHTS_v2",
        family="triage_weights",
        version=2,
        status="shadow",
        metadata={
            "amount_variance_weight": 0.5,
            "match_status_weight": 0.5,
        },
    ),
)


S2P_EVOLVER_CONFIG = PromptEvolverConfig(
    categories=list(S2PDomainConfig.categories),
    exploration_constant=1.414,
    promotion_improvement_threshold=0.05,
    promotion_min_samples=50,
)
