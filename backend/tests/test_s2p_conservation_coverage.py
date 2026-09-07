"""Regression tests for S2P's shared conservation coverage provider."""

from __future__ import annotations

from types import SimpleNamespace

from copilot_sdk.backend import conservation_utils

from app.routers import s2p


class _Store:
    def __init__(
        self,
        categories_with_data: int,
        *,
        verified_count: int = 10,
        correct_count: int = 8,
        total_decisions: int | None = None,
    ) -> None:
        self.categories_with_data = categories_with_data
        self.verified_count = verified_count
        self.correct_count = correct_count
        self.total_decisions = verified_count if total_decisions is None else total_decisions

    def count_verified(self, domain: str) -> int:
        return self.verified_count

    def count_correct(self, domain: str) -> int:
        return self.correct_count

    def count_verified_decisions(self, domain: str) -> int:
        return self.total_decisions

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


def _request_for_store(store: _Store) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(graph_store=store)))


def _public_status_for_store(store: _Store) -> str:
    state = SimpleNamespace(graph_store=store)
    counts = s2p.cached_conservation_state_provider(state)
    payload = conservation_utils.compute_conservation_status_payload("s2p", counts)
    return str(payload["status"])


def test_internal_and_public_conservation_agree_for_zero_decisions() -> None:
    store = _Store(0, verified_count=0, correct_count=0, total_decisions=0)
    s2p._clear_score_conservation_status_cache()

    assert s2p._current_conservation_status(_request_for_store(store)) == _public_status_for_store(store)


def test_internal_and_public_conservation_agree_for_bootstrap_decisions() -> None:
    store = _Store(3, verified_count=7, correct_count=6, total_decisions=7)
    s2p._clear_score_conservation_status_cache()

    assert s2p._current_conservation_status(_request_for_store(store)) == _public_status_for_store(store)


def test_internal_and_public_conservation_agree_for_normal_volume() -> None:
    store = _Store(5, verified_count=60, correct_count=56, total_decisions=60)
    s2p._clear_score_conservation_status_cache()

    assert s2p._current_conservation_status(_request_for_store(store)) == _public_status_for_store(store)


def test_internal_conservation_passes_category_coverage_to_engine(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def capture_status(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(
            status="GREEN",
            signal=1.0,
            theta_min=0.5,
            headroom=0.5,
            passed=True,
        )

    monkeypatch.setattr(conservation_utils, "conservation_status", capture_status)
    store = _Store(4, verified_count=12, correct_count=11, total_decisions=12)
    s2p._clear_score_conservation_status_cache()

    assert s2p._current_conservation_status(_request_for_store(store)) == "GREEN"
    assert calls[-1]["categories_with_data"] == 4
    assert calls[-1]["total_categories"] == 5


def test_cold_start_status_keeps_learning_allowed() -> None:
    assert s2p._is_learning_paused("COLD_START") is False
