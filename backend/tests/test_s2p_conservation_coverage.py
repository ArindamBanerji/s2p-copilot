"""Regression tests for S2P's shared conservation coverage provider."""

from __future__ import annotations

from app.routers import s2p


class _Store:
    def __init__(self, categories_with_data: int) -> None:
        self.categories_with_data = categories_with_data

    def count_verified(self, domain: str) -> int:
        return 10

    def count_correct(self, domain: str) -> int:
        return 8

    def count_verified_decisions(self, domain: str) -> int:
        return 10

    def count_categories_with_n(self, domain: str, n: int = 1) -> int:
        assert domain == "s2p"
        assert n == 1
        return self.categories_with_data


def test_s2p_conservation_provider_preserves_category_coverage() -> None:
    payload = s2p._read_conservation_counts(_Store(3), "s2p")

    assert payload["categories_total"] == 5
    assert payload["categories_with_data"] == 3
    assert payload["verified_count"] == 10


def test_s2p_conservation_provider_clamps_coverage_to_domain() -> None:
    payload = s2p._read_conservation_counts(_Store(99), "s2p")

    assert payload["categories_with_data"] == payload["categories_total"] == 5
