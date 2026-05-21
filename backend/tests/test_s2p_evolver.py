from __future__ import annotations

from pathlib import Path

import pytest

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG, S2P_VARIANTS
from app.services import s2p_evolver


SERVICE_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "s2p_evolver.py"


@pytest.fixture(autouse=True)
def reset_evolver_state():
    s2p_evolver.reset_s2p_evolver()
    yield
    s2p_evolver.reset_s2p_evolver()


def _stats(variant_id: str) -> dict:
    summary = s2p_evolver.get_evolution_summary()
    return next(variant for variant in summary["variants"] if variant["id"] == variant_id)


def test_s2p_evolver_config_has_expected_categories():
    assert S2P_EVOLVER_CONFIG.categories == list(S2PDomainConfig.categories)


def test_s2p_variants_registered_4_total():
    assert len(S2P_VARIANTS) == 4
    assert len(s2p_evolver.get_registered_variants()) == 4


def test_s2p_two_families_two_variants_each():
    variants = s2p_evolver.get_registered_variants()
    families = {}
    for variant in variants:
        families.setdefault(variant["family"], []).append(variant)

    assert set(families) == {"evidence_ordering", "routing_threshold"}
    assert {family: len(rows) for family, rows in families.items()} == {
        "evidence_ordering": 2,
        "routing_threshold": 2,
    }


def test_s2p_evolver_selects_active_variant():
    variant = s2p_evolver.get_active_variant(category="price_variance")

    assert variant is not None
    assert variant["status"] == "active"
    assert variant["id"] in {"EVIDENCE_ORDER_v1", "ROUTING_THRESHOLD_v1"}


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
    for _ in range(S2P_EVOLVER_CONFIG.promotion_min_samples):
        s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True, category="price_variance")
        s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v2", is_correct=True, category="price_variance")

    assert s2p_evolver.check_promotion() is None


def test_s2p_reset_re_registers_variants():
    s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True, category="price_variance")

    s2p_evolver.reset_s2p_evolver()

    assert len(s2p_evolver.get_registered_variants()) == 4
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
