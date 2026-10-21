"""Regression coverage for the S2P demo preseed phase order."""

import inspect


def test_boundary_before_freeze_and_review_after() -> None:
    """Keep budget learning before freeze and review learning after freeze."""
    from scripts.preseed_s2p_demo import run

    source = inspect.getsource(run)
    boundary_pos = source.index("_seed_boundary_allocations")
    freeze_pos = source.index("frozen-twin/freeze")
    review_pos = source.index("_seed_review_learning")
    pending_pos = source.index("_seed_pending_cohort")
    persist_pos = source.index("/api/demo/s2p/learning/persist")

    assert boundary_pos < freeze_pos, "boundary allocations must run before freeze"
    assert review_pos > freeze_pos, "review learning must run after freeze"
    assert review_pos < persist_pos, "review learning must complete before persist"
    assert review_pos < pending_pos < persist_pos, (
        "score-only pending work must be planted after review and before persist"
    )
    after_review = source[review_pos:]
    assert "_seed_boundary_allocations" not in after_review, (
        "boundary allocations must not run after review learning"
    )


def test_preseed_dry_run() -> None:
    """Dry run validates the fixture and reports planned work."""
    from scripts.preseed_s2p_demo import run

    result = run("http://127.0.0.1:9999", dry_run=True)
    assert result["dry_run"] is True
    assert "planned_scores" in result
    assert result["planned_scores"] > 0
