import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from gae.calibration import compute_theta_min
from copilot_sdk.graph.memory_store import InMemoryGraphStore

from app.domains.s2p.config import S2P_CATEGORIES
from app.graph.s2p_graph_reader import S2PGraphReader
from app.main import app, build_s2p_scorer
from app.routers import s2p_performance

client = TestClient(app)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _seed_performance_store(store: InMemoryGraphStore) -> InMemoryGraphStore:
    decisions = [
        ("D-1", S2P_CATEGORIES[0], "auto_approve", True),
        ("D-2", S2P_CATEGORIES[1], "hold_for_review", False),
        ("D-3", S2P_CATEGORIES[0], "auto_approve", None),
    ]
    for decision_id, category, action, is_correct in decisions:
        store.write_decision(
            "s2p",
            category,
            action,
            0.8,
            {"amount": 1.0},
            metadata={
                "decision_id": decision_id,
                "category_index": 0,
                "recommended_index": 0,
                "created_at": 1700000000.0,
            },
        )
        if is_correct is not None:
            store.write_outcome(decision_id, action, is_correct, domain="s2p")
    store.save_centroids(
        "s2p",
        "contract_gap",
        {"auto_approve": [0.1]},
        metadata={"decision_id": "D-1"},
    )
    store.save_centroids(
        "s2p",
        "duplicate_risk",
        {"hold_for_review": [0.2]},
        metadata={"decision_id": "D-2"},
    )
    return store


class SummaryGraphStore(InMemoryGraphStore):
    def __init__(self):
        super().__init__(domain="s2p")
        _seed_performance_store(self)


class SlowSummaryGraphStore(SummaryGraphStore):
    def __init__(self):
        super().__init__()
        self.summary_reads = 0

    def count_recommended_action(self, domain, action):
        assert domain == self.domain
        self.summary_reads += 1
        time.sleep(0.05)
        return super().count_recommended_action(domain, action)

    def get_all_decisions(self, domain: str | None = None):
        self.summary_reads += 1
        time.sleep(0.05)
        return super().get_all_decisions(domain)


class AggregateSummaryGraphStore(SummaryGraphStore):
    def get_all_decisions(self, domain: str | None = None):
        return super().get_all_decisions(domain)

    def get_verified_decisions(self, domain: str | None = None):
        raise AssertionError("summary should use aggregate counts when available")


def with_fake_store():
    original = app.state.graph_store
    fake = SummaryGraphStore()
    app.state.graph_store = fake
    app.state.s2p_graph_reader = S2PGraphReader(store=fake)
    s2p_performance.clear_summary_cache()
    return original


@pytest.fixture(autouse=True)
def reset_performance_state():
    yield
    scorer = build_s2p_scorer()
    app.state.scorer = scorer
    app.state.graph_store = scorer.graph_store
    app.state.s2p_graph_reader = S2PGraphReader(store=scorer.graph_store)
    s2p_performance.clear_summary_cache()


def test_trajectory_returns_points():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/trajectory")
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    assert response.status_code == 200
    data = response.json()
    assert data["total_checkpoints"] == 2
    assert data["verified"] == 2
    assert data["current_q"] == 0.5


def test_trajectory_empty_safe():
    original = app.state.graph_store
    unavailable = object()
    app.state.graph_store = unavailable
    app.state.s2p_graph_reader = S2PGraphReader(store=unavailable)
    try:
        response = client.get("/api/s2p/performance/trajectory")
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    assert response.status_code == 503


def test_what_if_returns_scenario():
    original = with_fake_store()
    try:
        response = client.get(
            "/api/s2p/performance/what-if",
            params={"additional_correct": 10, "additional_incorrect": 0},
        )
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    assert response.status_code == 200
    data = response.json()
    assert data["additional"]["correct"] == 10
    assert data["projected"]["verified"] == 12
    assert data["projected"]["q"] > data["current"]["q"]
    assert "theta_min" in data["projected"]


def test_what_if_uses_canonical_theta_min_formula():
    original = with_fake_store()
    try:
        response = client.get(
            "/api/s2p/performance/what-if",
            params={"additional_correct": 10, "additional_incorrect": 0},
        )
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    data = response.json()
    category_coverage = 2 / len(S2P_CATEGORIES)
    expected = round(compute_theta_min(category_coverage, 12), 4)
    assert data["projected"]["theta_min"] == expected


def test_performance_uses_compute_theta_min_not_penalty_denominator():
    source = (BACKEND_ROOT / "app" / "routers" / "s2p_performance.py").read_text(encoding="utf-8")
    assert "compute_theta_min" in source
    assert "PENALTY_RATIO * new_verified" not in source


def test_what_if_validates_input_bounds():
    response = client.get("/api/s2p/performance/what-if", params={"additional_correct": 101})

    assert response.status_code == 422


def test_summary_returns_metrics():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/summary")
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()
        s2p_performance.clear_summary_cache()

    assert response.status_code == 200
    data = response.json()
    assert data["total_scored"] == 3
    assert data["total_verified"] == 2
    assert data["accuracy"] == 0.5


def test_summary_uses_aggregate_counts_without_materializing_history():
    original = app.state.graph_store
    aggregate_store = AggregateSummaryGraphStore()
    app.state.graph_store = aggregate_store
    app.state.s2p_graph_reader = S2PGraphReader(store=aggregate_store)
    s2p_performance.clear_summary_cache()
    try:
        response = client.get("/api/s2p/performance/summary")
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    assert response.status_code == 200
    data = response.json()
    assert data["total_scored"] == 3
    assert data["total_verified"] == 2
    assert data["accuracy"] == 0.5
    assert data["auto_approve_rate"] == 0.6667


def test_auto_approve_rate_in_range():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/summary")
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    assert 0.0 <= response.json()["auto_approve_rate"] <= 1.0


def test_savings_estimate_positive_when_decisions_seeded():
    original = with_fake_store()
    try:
        response = client.get("/api/s2p/performance/summary")
    finally:
        app.state.graph_store = original

    assert response.json()["savings_estimate_usd"] > 0


def test_summary_concurrent_requests_coalesce_history_reads():
    original = app.state.graph_store
    fake = SlowSummaryGraphStore()
    app.state.graph_store = fake
    app.state.s2p_graph_reader = S2PGraphReader(store=fake)
    s2p_performance.clear_summary_cache()
    try:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _index: client.get("/api/s2p/performance/summary"), range(4)))
        elapsed = time.perf_counter() - started
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert fake.summary_reads == 1
    assert elapsed < 1.5


def test_summary_cache_expires(monkeypatch):
    original = app.state.graph_store
    fake = SlowSummaryGraphStore()
    app.state.graph_store = fake
    app.state.s2p_graph_reader = S2PGraphReader(store=fake)
    s2p_performance.clear_summary_cache()
    monkeypatch.setattr(s2p_performance, "SUMMARY_CACHE_TTL_SECONDS", 60.0)
    try:
        first = client.get("/api/s2p/performance/summary").json()
        cached = client.get("/api/s2p/performance/summary").json()
        key = s2p_performance._summary_cache_key(fake, fake.domain)
        with s2p_performance._SUMMARY_CACHE_LOCK:
            _timestamp, payload = s2p_performance._SUMMARY_CACHE[key]
            s2p_performance._SUMMARY_CACHE[key] = (time.monotonic() - 61.0, payload)
        refreshed = client.get("/api/s2p/performance/summary").json()
    finally:
        app.state.graph_store = original
        s2p_performance.clear_summary_cache()

    assert first == cached
    assert refreshed == first
    assert fake.summary_reads == 2


def test_all_performance_endpoints_200():
    paths = [
        "/api/s2p/performance/trajectory",
        "/api/s2p/performance/what-if",
        "/api/s2p/performance/summary",
    ]

    for path in paths:
        assert client.get(path).status_code == 200
