"""Contract tests for domain-scoped S2P LearningState reads."""

from __future__ import annotations

from pathlib import Path


def _learning_state_query_source() -> str:
    source = Path(__file__).parents[1].joinpath(
        "app", "routers", "framework_router.py"
    ).read_text(encoding="utf-8")
    assert "MATCH (ls:LearningState)" in source
    return source


def test_learning_state_query_scoped_to_s2p() -> None:
    source = _learning_state_query_source()

    assert "WHERE ls.domain = $domain" in source
    assert '"domain": FRAMEWORK_DOMAIN' in source
    assert 'FRAMEWORK_DOMAIN = "s2p"' in source


def test_learning_state_foreign_domain_excluded() -> None:
    source = _learning_state_query_source()

    assert "WHERE ls.domain = $domain" in source
    assert "MATCH (ls:LearningState) RETURN ls.warm_start_active" not in source
