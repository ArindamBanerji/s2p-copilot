from copilot_sdk.evolution import PromotionDecision

from app.domains.s2p.evolution import S2PEvolutionService


PROMOTED_ID = "auto_approve_threshold_sweep:price_variance:0.91"
LOSS_ID = "auto_approve_threshold_sweep:price_variance:0.79"


def test_autonomous_promotion_green_promotes_fixture_variant():
    service = S2PEvolutionService(scorer=None)

    result = service.evaluate_promotion("auto_approve_threshold_sweep", PROMOTED_ID)

    assert result.action == PromotionDecision.PROMOTE
    assert result.reason == "criteria_met"
    assert result.evidence["win_rate"] == 1.0


def test_autonomous_promotion_amber_blocks_fixture_variant():
    service = S2PEvolutionService(scorer=None)
    service.fixture["conservation_status"] = "AMBER"
    service.fixture["conservation_pass"] = False

    result = service.evaluate_promotion("auto_approve_threshold_sweep", PROMOTED_ID)

    assert result.action == PromotionDecision.BLOCK
    assert result.reason == "conservation"


def test_autonomous_promotion_shadow_losses_do_not_promote():
    service = S2PEvolutionService(scorer=None)

    result = service.evaluate_promotion("auto_approve_threshold_sweep", LOSS_ID)

    assert result.action != PromotionDecision.PROMOTE
    assert result.reason in {"regression", "win_rate"}
