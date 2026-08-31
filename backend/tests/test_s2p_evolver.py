from __future__ import annotations

from pathlib import Path

import pytest

from copilot_sdk.graph.memory_store import InMemoryGraphStore


class EvolverGraphStore(InMemoryGraphStore):
    """AGE-shaped registration reads in write order for variant reconstruction."""

    def get_evolution_events(self, domain: str, **kwargs: object) -> list[dict]:
        event_type = kwargs.get("event_type")
        events = super().get_evolution_events(domain, limit=kwargs.get("limit", 100))
        if isinstance(event_type, str):
            events = [event for event in events if event.get("event_type") == event_type]
        return list(events)

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG, S2P_VARIANTS
from app.services import s2p_evolver


SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "s2p_evolver.py"


@pytest.fixture(autouse=True)
def reset_evolver_state():
    class FixedProvider:
        def get_state(self) -> dict[str, str]:
            return {"status": "GREEN"}

        def __call__(self) -> dict[str, str]:
            return self.get_state()

    provider = FixedProvider()
    s2p_evolver.set_graph_store(EvolverGraphStore(domain="s2p"))
    s2p_evolver.set_conservation_provider(provider)
    s2p_evolver.reset_s2p_evolver()
    yield
    s2p_evolver.reset_s2p_evolver()
    s2p_evolver.set_conservation_provider(provider)


def _stats(variant_id: str) -> dict:
    summary = s2p_evolver.get_evolution_summary()
    return next(variant for variant in summary["variants"] if variant["id"] == variant_id)


def test_s2p_evolver_config_has_expected_categories():
    assert S2P_EVOLVER_CONFIG.categories == list(S2PDomainConfig.categories)


def test_s2p_variants_registered_8_total():
    assert len(S2P_VARIANTS) == 8
    assert len(s2p_evolver.get_registered_variants()) == 8


def test_s2p_four_families_two_variants_each():
    variants = s2p_evolver.get_registered_variants()
    families = {}
    for variant in variants:
        families.setdefault(variant["family"], []).append(variant)

    assert set(families) == {
        "evidence_ordering",
        "routing_threshold",
        "escalation_criteria",
        "triage_weights",
    }
    assert {family: len(rows) for family, rows in families.items()} == {
        "evidence_ordering": 2,
        "routing_threshold": 2,
        "escalation_criteria": 2,
        "triage_weights": 2,
    }
    assert {
        family: {variant["status"] for variant in rows}
        for family, rows in families.items()
    } == {
        "evidence_ordering": {"active", "shadow"},
        "routing_threshold": {"active", "shadow"},
        "escalation_criteria": {"active", "shadow"},
        "triage_weights": {"active", "shadow"},
    }


def test_s2p_supplement_preserves_original_variants():
    variants = {variant.id: variant for variant in S2P_VARIANTS}

    assert variants["EVIDENCE_ORDER_v1"].family == "evidence_ordering"
    assert variants["EVIDENCE_ORDER_v1"].version == 1
    assert variants["EVIDENCE_ORDER_v1"].status == "active"
    assert variants["EVIDENCE_ORDER_v1"].metadata == {
        "order": ["factor_fingerprint", "similar_invoices", "audit_trail"],
    }
    assert variants["EVIDENCE_ORDER_v2"].family == "evidence_ordering"
    assert variants["EVIDENCE_ORDER_v2"].version == 2
    assert variants["EVIDENCE_ORDER_v2"].status == "shadow"
    assert variants["EVIDENCE_ORDER_v2"].metadata == {
        "order": ["supplier_history", "contract_terms", "factor_fingerprint"],
    }
    assert variants["ROUTING_THRESHOLD_v1"].family == "routing_threshold"
    assert variants["ROUTING_THRESHOLD_v1"].version == 1
    assert variants["ROUTING_THRESHOLD_v1"].status == "active"
    assert variants["ROUTING_THRESHOLD_v1"].metadata == {
        "auto_approve_confidence": 0.86,
        "escalate_confidence": 0.68,
    }
    assert variants["ROUTING_THRESHOLD_v2"].family == "routing_threshold"
    assert variants["ROUTING_THRESHOLD_v2"].version == 2
    assert variants["ROUTING_THRESHOLD_v2"].status == "shadow"
    assert variants["ROUTING_THRESHOLD_v2"].metadata == {
        "auto_approve_confidence": 0.91,
        "escalate_confidence": 0.72,
    }


def test_s2p_supplement_new_variant_metadata():
    variants = {variant.id: variant for variant in S2P_VARIANTS}

    assert variants["ESCALATION_CRITERIA_v1"].metadata == {
        "missing_po_escalate": True,
        "amount_threshold": 50000,
    }
    assert variants["ESCALATION_CRITERIA_v2"].metadata == {
        "missing_po_escalate": True,
        "amount_threshold": 25000,
    }
    assert variants["ESCALATION_CRITERIA_v2"].metadata["amount_threshold"] < variants[
        "ESCALATION_CRITERIA_v1"
    ].metadata["amount_threshold"]
    assert all(
        variants[variant_id].metadata["missing_po_escalate"] is True
        for variant_id in ("ESCALATION_CRITERIA_v1", "ESCALATION_CRITERIA_v2")
    )

    for variant_id in ("TRIAGE_WEIGHTS_v1", "TRIAGE_WEIGHTS_v2"):
        metadata = variants[variant_id].metadata
        assert 0.0 <= metadata["amount_variance_weight"] <= 1.0
        assert 0.0 <= metadata["match_status_weight"] <= 1.0
        assert metadata["amount_variance_weight"] + metadata["match_status_weight"] == pytest.approx(1.0)


def test_s2p_supplement_config_promotes_after_50_samples():
    assert S2P_EVOLVER_CONFIG.promotion_min_samples == 50
    assert S2P_EVOLVER_CONFIG.promotion_min_samples != 10
    assert len({variant.id for variant in S2P_VARIANTS}) == len(S2P_VARIANTS)


def test_s2p_evolver_selects_active_variant():
    variant = s2p_evolver.get_active_variant(category="price_variance")

    assert variant is not None
    assert variant["status"] == "active"
    assert variant["id"] in {
        "EVIDENCE_ORDER_v1",
        "ROUTING_THRESHOLD_v1",
        "ESCALATION_CRITERIA_v1",
        "TRIAGE_WEIGHTS_v1",
    }


def test_s2p_get_active_variant_returns_metadata():
    evidence = s2p_evolver.get_active_variant(category="price_variance", family="evidence_ordering")
    routing = s2p_evolver.get_active_variant(category="price_variance", family="routing_threshold")

    assert evidence == {
        "id": "EVIDENCE_ORDER_v1",
        "family": "evidence_ordering",
        "version": 1,
        "status": "active",
        "metadata": {"order": ["factor_fingerprint", "similar_invoices", "audit_trail"]},
    }
    assert routing is not None
    assert routing["metadata"]["auto_approve_confidence"] == 0.86
    assert routing["metadata"]["escalate_confidence"] == 0.68


def test_s2p_record_outcome_positive_reward_is_success():
    s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", reward=0.5, category="price_variance")

    stats = _stats("EVIDENCE_ORDER_v1")
    assert stats["successes"] == 1
    assert stats["failures"] == 0
    assert stats["total"] == 1


def test_s2p_record_outcome_negative_reward_is_failure():
    s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", reward=-0.1, category="price_variance")

    stats = _stats("EVIDENCE_ORDER_v1")
    assert stats["successes"] == 0
    assert stats["failures"] == 1
    assert stats["total"] == 1


def test_s2p_record_outcome_explicit_is_correct_wins_over_reward():
    s2p_evolver.record_triage_outcome(
        "EVIDENCE_ORDER_v1",
        reward=-1.0,
        is_correct=True,
        category="price_variance",
    )

    stats = _stats("EVIDENCE_ORDER_v1")
    assert stats["successes"] == 1
    assert stats["failures"] == 0


def test_s2p_record_outcome_updates_stats():
    s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True, category="price_variance")
    s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=False, category="price_variance")

    stats = _stats("EVIDENCE_ORDER_v1")
    assert stats["successes"] == 1
    assert stats["failures"] == 1
    assert stats["total"] == 2
    assert stats["success_rate"] == pytest.approx(0.5)


def test_s2p_promotion_when_threshold_exceeded():
    for _ in range(S2P_EVOLVER_CONFIG.promotion_min_samples):
        s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v2", is_correct=True, category="price_variance")

    result = s2p_evolver.check_promotion()

    assert result is not None
    assert result["family"] == "evidence_ordering"
    assert result["promoted_id"] == "EVIDENCE_ORDER_v2"
    assert result["previous_id"] == "EVIDENCE_ORDER_v1"
    statuses = {variant["id"]: variant["status"] for variant in s2p_evolver.get_registered_variants()}
    assert statuses["EVIDENCE_ORDER_v2"] == "active"
    assert statuses["EVIDENCE_ORDER_v1"] == "retired"


def test_s2p_no_promotion_below_threshold():
    for _ in range(S2P_EVOLVER_CONFIG.promotion_min_samples - 1):
        s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True, category="price_variance")
        s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v2", is_correct=True, category="price_variance")

    assert s2p_evolver.check_promotion({"status": "GREEN"}) is None


def test_s2p_reset_re_registers_variants():
    s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True, category="price_variance")

    s2p_evolver.reset_s2p_evolver()

    assert len(s2p_evolver.get_registered_variants()) == 8
    assert s2p_evolver.get_active_variant(family="evidence_ordering")["id"] == "EVIDENCE_ORDER_v1"
    assert _stats("EVIDENCE_ORDER_v1")["total"] == 0


def test_s2p_evolver_has_no_level1_imports():
    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert "ProfileScorer" not in source
    assert "CompoundingScorer" not in source
    assert "centroid" not in source
    assert "_scorer" not in source


def test_s2p_evolver_uses_sdk_prompt_evolver():
    source = SERVICE_PATH.read_text(encoding="utf-8")

    assert "PromptVariantEvolver" in source
    assert isinstance(s2p_evolver._s2p_evolver, s2p_evolver.PromptVariantEvolver)
