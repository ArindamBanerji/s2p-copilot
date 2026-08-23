"""
tests/test_s2p_score_endpoint.py — POST /api/s2p/score endpoint tests.

Run from backend/:
    pytest tests/test_s2p_score_endpoint.py -v
"""

import json
import sys
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient
from app.main import app, build_s2p_scorer
from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import S2PGraphReader
from app.routers import s2p as s2p_router
from app.services.s2p_evolver import get_evolution_summary, reset_s2p_evolver
from app.services.supplier_profile_accumulator import accumulator as supplier_profile_accumulator

client = TestClient(app)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"

VALID_REQUEST = {
    "event_id": "E001",
    "category": "price_variance",
    "amount": 5000.0,
    "supplier_id": "SUP-001",
    "match_status": 0.92,
    "amount_variance_ratio": 0.08,
    "duplicate_score": 0.04,
    "supplier_exception_history": 0.05,
    "payment_terms_impact": 0.48,
    "commodity_index_correlation": 0.76,
    "tax_regulatory_compliance": 0.90,
}


def test_score_timeout_returns_503_when_same_invoice_lock_is_held():
    lock_manager = s2p_router._S2P_SCORE_LOCKS
    assert lock_manager.acquire(VALID_REQUEST["event_id"], timeout=1.0)
    try:
        started = time.perf_counter()
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
        elapsed = time.perf_counter() - started
    finally:
        lock_manager.release(VALID_REQUEST["event_id"])

    assert response.status_code == 503
    assert response.json()["detail"] == f"Score path busy for {VALID_REQUEST['event_id']} — retry"
    assert elapsed < 2.5


def reset_sdk_scorer():
    app.state.scorer = build_s2p_scorer()
    app.state.graph_store = app.state.scorer.graph_store
    app.state.s2p_graph_reader = S2PGraphReader(store=app.state.scorer.graph_store)
    app.state.s2p_reward_function = app.state.scorer._reward_fn
    s2p_router._clear_score_conservation_status_cache()
    reset_s2p_evolver()
    supplier_profile_accumulator.reset()
    return app.state.scorer


class GraphContextStore:
    """Complete graph-store overlay used by context-specific endpoint tests."""

    domain = "s2p"

    def __init__(self, delegate, result=None, failure: Exception | None = None):
        self._delegate = delegate
        self._result = result
        self._failure = failure

    def query_context(self, entity_id, hops, domain: str | None = None):
        assert domain == self.domain
        if self._failure is not None:
            raise self._failure
        return list(self._result or [])

    def __getattr__(self, name):
        return getattr(self._delegate, name)


def score_for_learn(action_payload=None):
    reset_sdk_scorer()
    response = client.post("/api/s2p/score", json={**VALID_REQUEST, **(action_payload or {})})
    assert response.status_code == 200
    return response.json()


def test_score_endpoint_returns_200():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    assert response.status_code == 200


def test_score_response_has_required_fields():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    for key in ("event_id", "category", "action", "action_index",
                "confidence", "probabilities", "factor_vector", "factor_names"):
        assert key in data, f"Missing key: {key}"


class SlowScoreScorer:
    def __init__(self, score_sleep: float = 0.05):
        self.score_sleep = score_sleep
        self.graph_store = SimpleNamespace()
        self.centroids = [
            [
                [0.5 for _factor in S2PDomainConfig.factors]
                for _action in S2PDomainConfig.actions
            ]
            for _category in S2PDomainConfig.categories
        ]

    def score(self, factors, category, metadata=None):
        time.sleep(self.score_sleep)
        return SimpleNamespace(
            action="auto_approve",
            action_index=0,
            confidence=0.91,
            probabilities=[0.91, 0.03, 0.02, 0.02, 0.02],
            decision_id=f"S2P-TEST-{uuid4().hex[:8]}",
        )


def _install_fast_score_dependencies(monkeypatch, scorer=None, submit_side_effects: bool = False):
    scorer = scorer or SlowScoreScorer()
    monkeypatch.setattr(app.state, "scorer", scorer, raising=False)
    monkeypatch.setattr(app.state, "graph_store", scorer.graph_store, raising=False)
    monkeypatch.setattr(s2p_router, "_resolve_graph_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(s2p_router, "_apply_cross_copilot_signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(s2p_router, "_score_conservation_status", lambda *_args, **_kwargs: "GREEN")
    monkeypatch.setattr(s2p_router, "get_active_variant", lambda **_kwargs: None)
    monkeypatch.setattr(s2p_router, "_link_decision_to_invoice", lambda *_args, **_kwargs: None)
    if not submit_side_effects:
        monkeypatch.setattr(s2p_router, "_submit_side_effect", lambda fn: None)
    monkeypatch.setattr(s2p_router, "apply_cache_invalidation_event", lambda *_args, **_kwargs: [])
    return scorer


def test_score_concurrent_not_serialized_on_enrichment(monkeypatch):
    _install_fast_score_dependencies(monkeypatch, SlowScoreScorer(score_sleep=0.05))

    def slow_process_context(_signal):
        time.sleep(0.5)
        return None

    monkeypatch.setattr(s2p_router, "_score_process_context_with_signal", slow_process_context)
    payloads = [
        {**VALID_REQUEST, "event_id": f"E-CONCURRENT-ENRICH-{index}", "amount": 5000.0 + index}
        for index in range(2)
    ]
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda payload: client.post("/api/s2p/score", json=payload), payloads))

    elapsed = time.perf_counter() - started
    assert all(response.status_code == 200 for response in responses)
    assert elapsed < 1.0


def test_fire_and_forget_failure_logged(monkeypatch, caplog):
    _install_fast_score_dependencies(monkeypatch, SlowScoreScorer(score_sleep=0.01), submit_side_effects=True)

    def failing_shadow(*_args, **_kwargs):
        raise RuntimeError("shadow write failed")

    monkeypatch.setattr(s2p_router, "_record_score_shadow", failing_shadow)

    with caplog.at_level("WARNING"):
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
        deadline = time.time() + 2.0
        while time.time() < deadline and "S2P side effect failed" not in caplog.text:
            time.sleep(0.01)

    assert response.status_code == 200
    assert "S2P side effect failed" in caplog.text


def test_response_shape_unchanged():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    expected = {
        "event_id",
        "category",
        "action",
        "action_index",
        "confidence",
        "probabilities",
        "factor_vector",
        "factor_names",
        "decision_id",
        "process_context",
        "active_variant",
        "auto_approve",
        "novelty_score",
        "threshold_decision",
    }
    assert response.status_code == 200
    assert expected <= set(data)


def test_enrichment_failure_doesnt_break_score(monkeypatch):
    _install_fast_score_dependencies(monkeypatch, SlowScoreScorer(score_sleep=0.01))

    def broken_process_context(_signal):
        raise RuntimeError("process context unavailable")

    monkeypatch.setattr(s2p_router, "_score_process_context_with_signal", broken_process_context)

    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()

    assert response.status_code == 200
    assert data["process_context"] is None
    assert data["decision_id"]


def test_score_response_includes_active_variant():
    reset_sdk_scorer()

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    active_variant = response.json()["active_variant"]
    assert active_variant["id"] == "EVIDENCE_ORDER_v1"
    assert active_variant["family"] == "evidence_ordering"
    assert active_variant["metadata"]["order"] == ["factor_fingerprint", "similar_invoices", "audit_trail"]


def test_score_response_includes_cached_conservation_status(monkeypatch):
    reset_sdk_scorer()
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "GREEN")
    s2p_router._score_conservation_status(SimpleNamespace(app=app))

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["auto_approve"]["conservation_status"] == "GREEN"


def test_score_conservation_status_cache_expires(monkeypatch):
    reset_sdk_scorer()
    calls = []

    def status(_request):
        calls.append(time.monotonic())
        return f"GREEN-{len(calls)}"

    monkeypatch.setattr(s2p_router, "_current_conservation_status", status)
    monkeypatch.setattr(s2p_router, "_SCORE_CONSERVATION_STATUS_TTL_SECONDS", 0.01)
    request = SimpleNamespace(app=app)

    first = s2p_router._score_conservation_status(request)
    cached = s2p_router._score_conservation_status(request)
    time.sleep(0.02)
    refreshed = s2p_router._score_conservation_status(request)

    assert first == "GREEN-1"
    assert cached == "GREEN-1"
    assert refreshed == "GREEN-2"
    assert len(calls) == 2


def test_concurrent_score_requests_coalesce_conservation_status(monkeypatch):
    reset_sdk_scorer()
    calls = 0
    calls_lock = threading.Lock()

    def slow_status(_request):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return "GREEN"

    monkeypatch.setattr(s2p_router, "_current_conservation_status", slow_status)
    payloads = [
        {**VALID_REQUEST, "event_id": f"E-CONCURRENT-{index}", "amount": 5000.0 + index}
        for index in range(4)
    ]

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda payload: client.post("/api/s2p/score", json=payload), payloads))
    elapsed = time.perf_counter() - started

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert calls == 0
    assert elapsed < 1.5


def test_concurrent_full_conservation_requests_do_not_serialize_counts(monkeypatch):
    reset_sdk_scorer()
    calls = 0
    calls_lock = threading.Lock()

    def slow_counts(_graph_store, _domain=None):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {
            "verified_count": 12,
            "correct_count": 10,
            "total_decisions": 12,
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", slow_counts)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _index: client.get("/api/conservation/status"), range(4)))
    elapsed = time.perf_counter() - started

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert 1 <= calls <= 4
    assert elapsed < 1.5


def test_browser_like_conservation_and_score_waterfall_shares_counts_cache(monkeypatch):
    reset_sdk_scorer()
    # Startup materialization may already have a ready conservation entry.
    # Clear that entry so this test exercises the shared counts computation
    # deterministically, without depending on startup timing.
    app.state.s2p_tab_state_cache.delete_standard("score")
    calls = 0
    calls_lock = threading.Lock()

    def slow_counts(_graph_store, _domain=None):
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {
            "verified_count": 12,
            "correct_count": 10,
            "total_decisions": 12,
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", slow_counts)

    requests = [
        lambda: client.get("/api/s2p/preview/queue"),
        lambda: client.get("/api/s2p/preview/conservation"),
        lambda: client.get("/api/conservation/status"),
        lambda: client.post("/api/s2p/score", json=VALID_REQUEST),
    ]

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda call: call(), requests))
    elapsed = time.perf_counter() - started

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    conservation = responses[2].json()
    assert conservation["verified_count"] == 12
    assert conservation["correct_count"] == 10
    assert responses[-1].json()["auto_approve"]["conservation_status"]
    assert 1 <= calls <= 4
    assert elapsed < 1.5


def test_read_conservation_counts_uses_verified_decision_count_for_conservation_v():
    class Store:
        domain = "s2p"

        def count_verified(self, domain):
            assert domain == "s2p"
            return 2

        def count_correct(self, domain):
            assert domain == "s2p"
            return 1

        def count_verified_decisions(self, domain):
            assert domain == "s2p"
            return 2

        def count_decisions(self, domain):
            assert domain == "s2p"
            return 7

        def get_all_decisions(self, domain: str | None = None):
            raise AssertionError("get_all_decisions should not be used for conservation V")

        def get_decision(self, decision_id: str, domain: str | None = None):
            return None

        def write_outcome(
            self,
            decision_id: str,
            actual_action: str,
            is_correct: bool,
            metadata: dict | None = None,
            domain: str | None = None,
        ) -> None:
            raise AssertionError("conservation route must not write outcomes")

        def get_archived_decisions(self, domain: str):
            return []

    counts = s2p_router._read_conservation_counts(Store(), "s2p")

    assert counts["verified_count"] == 2
    assert counts["correct_count"] == 1
    assert counts["total_decisions"] == 2
    assert counts["penalty_ratio"] == 5.0


def test_read_conservation_counts_falls_back_to_verified_count_not_all_rows():
    class Store:
        domain = "s2p"

        def count_verified(self, domain):
            assert domain == "s2p"
            return 2

        def count_correct(self, domain):
            assert domain == "s2p"
            return 1

        def count_verified_decisions(self, domain):
            assert domain == "s2p"
            return 2

        def count_decisions(self, domain):
            raise AssertionError("all-row count_decisions must not define conservation V")

        def get_all_decisions(self, domain: str | None = None):
            raise AssertionError("all-row get_all_decisions must not define conservation V")

        def get_decision(self, decision_id: str, domain: str | None = None):
            return None

        def write_outcome(
            self,
            decision_id: str,
            actual_action: str,
            is_correct: bool,
            metadata: dict | None = None,
            domain: str | None = None,
        ) -> None:
            raise AssertionError("conservation route must not write outcomes")

        def get_archived_decisions(self, domain: str):
            return []

    counts = s2p_router._read_conservation_counts(Store(), "s2p")

    assert counts["verified_count"] == 2
    assert counts["correct_count"] == 1
    assert counts["total_decisions"] == 2


def test_full_conservation_counts_cache_expires(monkeypatch):
    reset_sdk_scorer()
    calls = []

    def counts(_graph_store, _domain=None):
        calls.append(time.monotonic())
        return {
            "verified_count": len(calls),
            "correct_count": len(calls),
            "total_decisions": len(calls),
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", counts)
    monkeypatch.setattr(s2p_router, "_SCORE_CONSERVATION_STATUS_TTL_SECONDS", 60.0)

    first = s2p_router.cached_conservation_state_provider(app.state)
    cached = s2p_router.cached_conservation_state_provider(app.state)
    cache_key = s2p_router._conservation_cache_key(app.state.graph_store, "s2p")
    _timestamp, cached_counts = s2p_router._CONSERVATION_COUNTS_CACHE[cache_key]
    s2p_router._CONSERVATION_COUNTS_CACHE[cache_key] = (
        time.monotonic() - 61.0,
        cached_counts,
    )
    refreshed = s2p_router.cached_conservation_state_provider(app.state)

    assert first["verified_count"] == 1
    assert cached["verified_count"] == 1
    assert refreshed["verified_count"] == 2
    assert len(calls) == 2


def test_conservation_cache_returns_value_within_ttl(monkeypatch):
    reset_sdk_scorer()
    calls = []

    def counts(_graph_store, _domain=None):
        calls.append(1)
        return {
            "verified_count": 7,
            "correct_count": 6,
            "total_decisions": 8,
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", counts)
    monkeypatch.setattr(s2p_router, "_SCORE_CONSERVATION_STATUS_TTL_SECONDS", 60.0)

    first = s2p_router.cached_conservation_state_provider(app.state)
    second = s2p_router.cached_conservation_state_provider(app.state)

    assert first == second
    assert first["verified_count"] == 7
    assert len(calls) == 1


def test_conservation_cache_recomputes_after_ttl(monkeypatch):
    reset_sdk_scorer()
    calls = []

    def counts(_graph_store, _domain=None):
        calls.append(1)
        return {
            "verified_count": len(calls),
            "correct_count": len(calls),
            "total_decisions": len(calls),
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", counts)
    monkeypatch.setattr(s2p_router, "_SCORE_CONSERVATION_STATUS_TTL_SECONDS", 0.1)

    first = s2p_router.cached_conservation_state_provider(app.state)
    time.sleep(0.15)
    refreshed = s2p_router.cached_conservation_state_provider(app.state)

    assert first["verified_count"] == 1
    assert refreshed["verified_count"] == 2
    assert len(calls) == 2


def test_conservation_cache_cleared_on_learn(monkeypatch):
    reset_sdk_scorer()
    calls = []

    def counts(_graph_store, _domain=None):
        calls.append(1)
        return {
            "verified_count": len(calls),
            "correct_count": len(calls),
            "total_decisions": len(calls),
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", counts)
    monkeypatch.setattr(s2p_router, "_SCORE_CONSERVATION_STATUS_TTL_SECONDS", 60.0)

    first = s2p_router.cached_conservation_state_provider(app.state)
    s2p_router._clear_score_conservation_status_cache()
    refreshed = s2p_router.cached_conservation_state_provider(app.state)

    assert first["verified_count"] == 1
    assert refreshed["verified_count"] == 2
    assert len(calls) == 2


def test_conservation_cache_readers_do_not_block_on_miss(monkeypatch):
    reset_sdk_scorer()
    monkeypatch.setattr(s2p_router, "_SCORE_CONSERVATION_STATUS_TTL_SECONDS", 0.01)
    key = s2p_router._conservation_cache_key(app.state.graph_store, "s2p")
    s2p_router._CONSERVATION_COUNTS_CACHE[key] = (
        time.monotonic() - 1.0,
        {
            "verified_count": 3,
            "correct_count": 2,
            "total_decisions": 4,
            "penalty_ratio": 5.0,
        },
    )
    started = threading.Event()
    release = threading.Event()

    def slow_counts(_graph_store, _domain=None):
        started.set()
        assert release.wait(2.0)
        return {
            "verified_count": 9,
            "correct_count": 8,
            "total_decisions": 10,
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", slow_counts)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(s2p_router.cached_conservation_state_provider, app.state)
        assert started.wait(2.0)
        began = time.perf_counter()
        stale = s2p_router.cached_conservation_state_provider(app.state)
        elapsed = time.perf_counter() - began
        release.set()
        future.result(timeout=2.0)

    assert elapsed < 0.1
    assert stale["verified_count"] == 3
    assert s2p_router.cached_conservation_state_provider(app.state)["verified_count"] == 9


def test_conservation_cache_miss_is_idempotent(monkeypatch):
    reset_sdk_scorer()
    calls = []

    def counts(_graph_store, _domain=None):
        calls.append(1)
        time.sleep(0.05)
        return {
            "verified_count": 11,
            "correct_count": 10,
            "total_decisions": 12,
            "penalty_ratio": 5.0,
        }

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", counts)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: s2p_router.cached_conservation_state_provider(app.state), range(2)))

    assert all(result["verified_count"] == 11 for result in results)
    assert s2p_router.cached_conservation_state_provider(app.state)["correct_count"] == 10
    assert 1 <= len(calls) <= 2


def test_current_conservation_status_failure_remains_unknown(monkeypatch):
    reset_sdk_scorer()

    def broken_counts(_graph_store, _domain=None):
        raise RuntimeError("conservation store unavailable")

    monkeypatch.setattr(s2p_router, "_read_conservation_counts", broken_counts)

    assert s2p_router._current_conservation_status(SimpleNamespace(app=app)) == "UNKNOWN"


def test_score_action_is_valid_s2p_action():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    action = response.json()["action"]
    assert action in [
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    ]


def test_score_factor_vector_length():
    response = client.post("/api/s2p/score", json=VALID_REQUEST)
    data = response.json()
    assert len(data["factor_vector"]) == S2PDomainConfig.n_factors
    assert len(data["factor_names"]) == S2PDomainConfig.n_factors
    assert data["factor_names"] == S2PDomainConfig.factors


def test_score_invalid_category_returns_422():
    bad_request = {**VALID_REQUEST, "category": "lateral_movement"}
    response = client.post("/api/s2p/score", json=bad_request)
    assert response.status_code == 422


def test_score_legacy_category_returns_422():
    bad_request = {**VALID_REQUEST, "category": "supplier_risk"}
    response = client.post("/api/s2p/score", json=bad_request)
    assert response.status_code == 422


def test_score_endpoint_uses_compute_all_factors(monkeypatch):
    calls = []
    known = {name: (idx + 1) / 10 for idx, name in enumerate(S2PDomainConfig.factors)}

    def fake_compute_all_factors(invoice, context=None):
        calls.append((invoice, context))
        return known

    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][1] is None
    assert response.json()["factor_vector"] == [
        known[name] for name in S2PDomainConfig.factors
    ]


def test_score_endpoint_uses_graph_context_when_available(monkeypatch):
    calls = []

    def fake_compute_all_factors(invoice, context=None):
        calls.append(context)
        return {name: 0.2 for name in S2PDomainConfig.factors}

    original_scorer = app.state.scorer
    original_store = original_scorer.graph_store
    original_graph_store = getattr(app.state, "graph_store", None)
    fake_store = GraphContextStore(
        original_store,
        result=[{"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}}],
    )
    app.state.scorer = build_s2p_scorer(graph_store=fake_store)
    app.state.graph_store = fake_store
    app.state.s2p_graph_reader = S2PGraphReader(store=fake_store)
    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)
    try:
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
    finally:
        app.state.scorer = original_scorer
        if original_graph_store is None:
            del app.state.graph_store
        else:
            app.state.graph_store = original_graph_store
        app.state.s2p_graph_reader = S2PGraphReader(store=original_store)

    assert response.status_code == 200
    assert calls == [{"neighbors": [{"node": {"_label": "PurchaseOrder", "po_id": "PO-1"}}]}]


def test_score_endpoint_graph_context_failure_degrades_gracefully(monkeypatch):
    calls = []

    def fake_compute_all_factors(invoice, context=None):
        calls.append(context)
        return {name: 0.3 for name in S2PDomainConfig.factors}

    original_scorer = app.state.scorer
    original_store = original_scorer.graph_store
    original_graph_store = getattr(app.state, "graph_store", None)
    fake_store = GraphContextStore(original_store, failure=RuntimeError("graph unavailable"))
    app.state.scorer = build_s2p_scorer(graph_store=fake_store)
    app.state.graph_store = fake_store
    app.state.s2p_graph_reader = S2PGraphReader(store=fake_store)
    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)
    try:
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
    finally:
        app.state.scorer = original_scorer
        if original_graph_store is None:
            del app.state.graph_store
        else:
            app.state.graph_store = original_graph_store
        app.state.s2p_graph_reader = S2PGraphReader(store=original_store)

    body = response.json()
    assert response.status_code == 200
    assert isinstance(body["decision_id"], str)
    assert "neighbors" not in body
    assert calls == [None]


def test_score_endpoint_graph_context_timeout_degrades_gracefully(monkeypatch):
    def fake_compute_all_factors(invoice, context=None):
        return {name: 0.3 for name in S2PDomainConfig.factors}

    original_scorer = app.state.scorer
    original_store = original_scorer.graph_store
    original_graph_store = getattr(app.state, "graph_store", None)
    fake_store = GraphContextStore(original_store)

    def slow_query_context(entity_id, hops, domain=None):
        time.sleep(0.01)
        raise RuntimeError("graph timeout")

    fake_store.query_context = slow_query_context
    app.state.scorer = build_s2p_scorer(graph_store=fake_store)
    app.state.graph_store = fake_store
    app.state.s2p_graph_reader = S2PGraphReader(store=fake_store)
    monkeypatch.setattr(s2p_router, "compute_all_factors", fake_compute_all_factors)
    try:
        response = client.post("/api/s2p/score", json=VALID_REQUEST)
    finally:
        app.state.scorer = original_scorer
        if original_graph_store is None:
            del app.state.graph_store
        else:
            app.state.graph_store = original_graph_store
        app.state.s2p_graph_reader = S2PGraphReader(store=original_store)

    body = response.json()
    assert response.status_code == 200
    assert isinstance(body["decision_id"], str)


def test_score_endpoint_uses_fixture_invoice_factors_when_no_graph():
    invoice = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))[0]
    payload = {
        "event_id": invoice["invoice_id"],
        "category": invoice["category"],
        "amount": invoice["amount"],
        "supplier_id": invoice["supplier_id"],
    }

    response = client.post("/api/s2p/score", json=payload)

    assert response.status_code == 200
    assert response.json()["factor_vector"] == [
        invoice["factors"].get(name, 0.5) for name in S2PDomainConfig.factors
    ]


def test_score_endpoint_graph_lookup_uses_fixture_invoice_id(monkeypatch):
    invoice = json.loads((DATA_DIR / "synthetic_invoices.json").read_text(encoding="utf-8"))[0]
    seen = []

    original_scorer = app.state.scorer
    original_store = original_scorer.graph_store
    original_graph_store = getattr(app.state, "graph_store", None)
    fake_store = GraphContextStore(original_store, result=[])
    original_query_context = fake_store.query_context

    def recording_query_context(invoice_id, hops, domain=None):
        seen.append((invoice_id, hops))
        return original_query_context(invoice_id, hops, domain)

    fake_store.query_context = recording_query_context
    app.state.scorer = build_s2p_scorer(graph_store=fake_store)
    app.state.graph_store = fake_store
    app.state.s2p_graph_reader = S2PGraphReader(store=fake_store)
    try:
        response = client.post(
            "/api/s2p/score",
            json={
                "event_id": invoice["invoice_id"],
                "category": invoice["category"],
                "amount": invoice["amount"],
                "supplier_id": invoice["supplier_id"],
            },
        )
    finally:
        app.state.scorer = original_scorer
        if original_graph_store is None:
            del app.state.graph_store
        else:
            app.state.graph_store = original_graph_store
        app.state.s2p_graph_reader = S2PGraphReader(store=original_store)

    assert response.status_code == 200
    assert seen == [(invoice["invoice_id"], 2)]


def test_reward_function_wired_in_scorer():
    reset_sdk_scorer()
    reward_function = getattr(app.state, "s2p_reward_function", None)

    assert reward_function is not None
    assert reward_function.name == "s2p_graded_financial"
    assert reward_function.compute("auto_approve", "auto_approve", {}) == 1.0
    assert app.state.scorer._reward_fn is reward_function
    assert app.state.scorer._credit is not None
    assert app.state.scorer._explorer is not None


def test_sdk_learn_route_exists_and_returns_reward_fields():
    scored = score_for_learn()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reward"] == 0.8
    assert data["reward_raw"] == 0.8


def test_learn_response_contains_required_fields():
    scored = score_for_learn()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    data = response.json()
    for key in ("decision_id", "reward", "reward_raw"):
        assert key in data, f"Missing key: {key}"
    assert "decisions_total" in data


def test_learn_with_variant_id_records_outcome(monkeypatch):
    scored = score_for_learn()
    variant_id = scored["active_variant"]["id"]
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "GREEN")

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "variant_id": variant_id,
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["evolution_recorded"] is True
    assert data["active_variant_id"] == variant_id
    variant = next(row for row in get_evolution_summary()["variants"] if row["id"] == variant_id)
    assert variant["successes"] == 1
    assert variant["total"] == 1


def test_learn_with_variant_id_records_evolver_outcome(monkeypatch):
    scored = score_for_learn()
    variant_id = scored["active_variant"]["id"]
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "GREEN")

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "variant_id": variant_id,
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    variant = next(row for row in get_evolution_summary()["variants"] if row["id"] == variant_id)
    assert variant["successes"] == 1
    assert variant["total"] == 1


def test_learn_without_variant_id_backwards_compatible():
    scored = score_for_learn()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reward"] == 0.8
    assert data["evolution_recorded"] is False
    assert data["evolution_note"] == "variant_id not provided"


def test_learn_hook_fires_accumulator():
    scored = score_for_learn()

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    history = supplier_profile_accumulator.get_supplier_history(VALID_REQUEST["supplier_id"])
    assert len(history) == 1
    assert history[0]["supplier_id"] == VALID_REQUEST["supplier_id"]
    assert history[0]["category"] == VALID_REQUEST["category"]


def test_accumulator_failure_does_not_break_learn(monkeypatch):
    scored = score_for_learn()

    def fail_update(*args, **kwargs):
        raise RuntimeError("accumulator unavailable")

    monkeypatch.setattr(s2p_router.supplier_profile_accumulator, "on_decision_verified", fail_update)

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 80},
        },
    )

    assert response.status_code == 200
    assert response.json()["reward"] == 0.8


def test_supplier_id_missing_skips_gracefully_in_hook():
    reset_sdk_scorer()
    before = supplier_profile_accumulator.skipped_missing_supplier_id

    s2p_router._record_supplier_profile(
        {"decision_id": "D-MISSING-SUP", "recommended_action": "auto_approve", "metadata": {}},
        {"reward": 1.0},
        "auto_approve",
        {},
    )

    assert supplier_profile_accumulator.skipped_missing_supplier_id == before + 1


def test_positive_reward_records_success(monkeypatch):
    scored = score_for_learn()
    variant_id = scored["active_variant"]["id"]
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "GREEN")

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": scored["action"],
            "outcome": "confirmed",
            "variant_id": variant_id,
            "context": {"recovery_pct": 100},
        },
    )

    assert response.status_code == 200
    variant = next(row for row in get_evolution_summary()["variants"] if row["id"] == variant_id)
    assert variant["successes"] == 1
    assert variant["failures"] == 0


def test_negative_reward_records_failure(monkeypatch):
    scored = score_for_learn()
    variant_id = scored["active_variant"]["id"]
    override_action = next(action for action in S2PDomainConfig.actions if action != scored["action"])
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "GREEN")

    response = client.post(
        "/api/learn",
        json={
            "decision_id": scored["decision_id"],
            "actual_action": override_action,
            "outcome": "override",
            "reason_code": "wrong_action",
            "variant_id": variant_id,
            "context": {"amount": 1000, "at_risk": 250, "recovery_pct": 0},
        },
    )

    assert response.status_code == 200
    variant = next(row for row in get_evolution_summary()["variants"] if row["id"] == variant_id)
    assert variant["successes"] == 0
    assert variant["failures"] == 1


def test_conservation_status_endpoint_exists():
    reset_sdk_scorer()

    response = client.get("/api/conservation/status")

    assert response.status_code == 200
    assert response.json()["domain"] == "s2p"


def test_score_includes_process_context_when_available(monkeypatch):
    monkeypatch.setattr(s2p_router, "_SCORE_PROCESS_CONTEXT_CACHE", None)
    monkeypatch.setattr(
        s2p_router,
        "_load_celonis_cache",
        lambda: {
            "activities": [
                {
                    "id": "match_invoice_to_gr",
                    "name": "Match Invoice to GR",
                    "avg_duration_hours": 42.0,
                    "bottleneck": True,
                    "bottleneck_cause": "MATKL_V2",
                }
            ]
        },
    )

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    process_context = response.json()["process_context"]
    assert process_context["bottleneck_activity"] == "Match Invoice to GR"
    assert process_context["duration_median_min"] == 2520.0
    assert process_context["cause"] == "MATKL_V2"
    assert process_context["source"] == "celonis_cache"


def test_score_omits_process_context_when_unavailable(monkeypatch):
    monkeypatch.setattr(s2p_router, "_SCORE_PROCESS_CONTEXT_CACHE", None)
    monkeypatch.setattr(s2p_router, "_load_celonis_cache", lambda: {})

    response = client.post("/api/s2p/score", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json()["process_context"] is None


def test_outcome_returns_reward_fields():
    scored = score_for_learn()
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
            "amount": 1000,
            "at_risk": 1000,
            "recovery_pct": 80,
        },
    )

    assert response.status_code == 200
    assert response.json()["reward"] == 0.8
    assert response.json()["reward_raw"] == 0.8
    assert response.json()["evolution_recorded"] is False


def test_outcome_with_variant_id_records_evolver_outcome(monkeypatch):
    scored = score_for_learn()
    variant_id = scored["active_variant"]["id"]
    monkeypatch.setattr(s2p_router, "_current_conservation_status", lambda _request: "GREEN")
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
            "amount": 1000,
            "at_risk": 1000,
            "recovery_pct": 80,
            "variant_id": variant_id,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["evolution_recorded"] is True
    assert data["active_variant_id"] == variant_id
    variant = next(row for row in get_evolution_summary()["variants"] if row["id"] == variant_id)
    assert variant["successes"] == 1
    assert variant["total"] == 1


def test_outcome_hook_fires_accumulator():
    scored = score_for_learn()

    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": VALID_REQUEST["category"],
            "predicted_action": scored["action"],
            "amount": 1000,
            "at_risk": 1000,
            "recovery_pct": 80,
        },
    )

    assert response.status_code == 200
    history = supplier_profile_accumulator.get_supplier_history(VALID_REQUEST["supplier_id"])
    assert len(history) == 1
    assert history[0]["supplier_id"] == VALID_REQUEST["supplier_id"]
    assert history[0]["category"] == VALID_REQUEST["category"]


def test_outcome_without_variant_id_backwards_compatible():
    scored = score_for_learn()
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["reward"] > 0
    assert data["evolution_recorded"] is False
    assert data["evolution_note"] == "variant_id not provided"


def test_outcome_variant_id_optional_in_request_model():
    request = s2p_router.OutcomeRequest(
        decision_id="S2P-1",
        outcome="confirm",
        analyst_action="auto_approve",
        analyst_id="A001",
        factor_vector=[0.1] * S2PDomainConfig.n_factors,
        category="price_variance",
        predicted_action="auto_approve",
    )

    assert request.variant_id is None


def test_outcome_confirmed_positive_reward():
    scored = score_for_learn()
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "confirm",
            "analyst_action": scored["action"],
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
        },
    )

    assert response.status_code == 200
    assert response.json()["reward"] > 0


def test_outcome_overridden_negative_reward():
    scored = score_for_learn()
    override_action = next(action for action in S2PDomainConfig.actions if action != scored["action"])
    response = client.post(
        "/api/s2p/outcome",
        json={
            "decision_id": scored["decision_id"],
            "outcome": "override",
            "analyst_action": override_action,
            "analyst_id": "A001",
            "factor_vector": scored["factor_vector"],
            "category": "price_variance",
            "predicted_action": scored["action"],
            "amount": 1000,
            "at_risk": 250,
            "reason_code": "wrong_action",
        },
    )

    assert response.status_code == 200
    assert response.json()["reward_raw"] == -0.25
    assert response.json()["reward"] == -1.25


def test_learn_context_isolated_between_requests():
    first = score_for_learn()
    first_response = client.post(
        "/api/learn",
        json={
            "decision_id": first["decision_id"],
            "actual_action": first["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 25},
        },
    )

    second = score_for_learn()
    second_response = client.post(
        "/api/learn",
        json={
            "decision_id": second["decision_id"],
            "actual_action": second["action"],
            "outcome": "confirmed",
            "context": {"recovery_pct": 90},
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert first_response.json()["reward_raw"] == 0.25
    assert second_response.json()["reward_raw"] == 0.9


def test_learn_with_scorer_uses_context_without_mutating_reward_function():
    class GraphStore:
        def get_decision(self, decision_id: str, domain: str | None = None):
            if domain is not None:
                assert domain == "s2p"
            return {
                "decision_id": decision_id,
                "recommended_action": "auto_approve",
                "metadata": {"invoice_id": "S2P-INV-TEST"},
            }

        def get_decision_links(
            self,
            decision_id: str | None = None,
            domain: str | None = None,
            limit: int | None = None,
        ) -> list[dict]:
            assert domain == "s2p"
            return []

        def write_outcome(
            self,
            decision_id: str,
            actual_action: str,
            is_correct: bool,
            metadata: dict | None = None,
            domain: str | None = None,
        ) -> None:
            raise AssertionError("outcome writes are not part of this double")

        def get_archived_decisions(self, domain: str):
            assert domain == "s2p"
            return []

    class RecordingScorer:
        def __init__(self):
            object.__setattr__(self, "graph_store", GraphStore())
            object.__setattr__(self, "_reward_fn", object())
            object.__setattr__(self, "reward_assignments", 0)
            object.__setattr__(self, "learn_contexts", [])

        def __setattr__(self, name, value):
            if name == "_reward_fn":
                self.reward_assignments += 1
            object.__setattr__(self, name, value)

        def learn(self, decision_id, actual_action, outcome, *, context=None):
            self.learn_contexts.append(dict(context or {}))
            return {
                "decision_id": decision_id,
                "reward": 0.4,
                "reward_raw": 0.4,
            }

    scorer = RecordingScorer()

    result = s2p_router._learn_with_scorer(
        scorer,
        "S2P-LOCK",
        "auto_approve",
        "confirmed",
        {"recovery_pct": 40},
    )

    assert result["reward_raw"] == 0.4
    assert result["reward"] == 0.4
    assert result["invoice_id"] == "S2P-INV-TEST"
    assert scorer.learn_contexts == [
        {
            "invoice_id": "S2P-INV-TEST",
            "recovery_pct": 40,
        }
    ]
    assert scorer.reward_assignments == 0
