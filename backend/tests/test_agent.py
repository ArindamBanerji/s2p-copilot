from app.domains.s2p.config import S2PDomainConfigV2
from app.framework.agent import SOCAgent


def test_agent_duplicate_risk_returns_canonical_s2p_action():
    result = SOCAgent().decide("duplicate_risk", {})

    assert result.action == "flag_leakage"
    assert result.action in S2PDomainConfigV2.actions
    assert result.action != "escalate_incident"
