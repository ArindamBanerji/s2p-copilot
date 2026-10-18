from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from copilot_sdk.graph.memory_store import InMemoryGraphStore
from app.services import response_materializer as module
from app.services.response_materializer import ResponseMaterializer
from app.services.s2p_materialization import create_s2p_materializer, conservation_payload


class TrackingStore(InMemoryGraphStore):
    def __init__(self):
        super().__init__(domain="s2p")
        self.reads = 0
        self.verified_reads = 0
        self.observations = 0

    def get_all_decisions(self, domain):
        self.reads += 1
        return super().get_all_decisions(domain)

    def get_verified_decisions(self, domain):
        self.verified_reads += 1
        return super().get_verified_decisions(domain)

    def write_observation(self, *args, **kwargs):
        self.observations += 1
        return super().write_observation(*args, **kwargs)


def test_concurrent_misses_share_one_refresh_and_own_nested_payloads():
    store = TrackingStore()
    started, release = threading.Event(), threading.Event()

    def compute(shared):
        started.set()
        assert release.wait(5)
        return {"rows": [{"value": len(shared["decisions"])}]}

    mat = ResponseMaterializer("s2p", lambda: store, {"queue": compute})
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(mat.get_or_refresh, "queue") for _ in range(10)]
        assert started.wait(5)
        release.set()
        results = [future.result(timeout=5) for future in futures]
    assert (store.reads, store.verified_reads) == (1, 1)
    results[0]["rows"][0]["value"] = 999
    assert mat.get("queue") == {"rows": [{"value": 0}]}
    assert all(result["rows"][0]["value"] == 0 for result in results[1:])


def test_ttl_starts_after_slow_build_and_empty_payload_is_a_hit(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    store = TrackingStore()

    def slow(shared):
        now[0] += 40.0
        return {}

    mat = ResponseMaterializer("s2p", lambda: store, {"empty": slow}, ttl=30)
    assert mat.get_or_refresh("empty") == {}
    now[0] += 29
    assert mat.get_or_refresh("empty") == {}
    assert store.reads == 1
    now[0] += 1
    assert mat.get("empty") is None
    assert mat.get_or_refresh("empty") == {}
    assert store.reads == 2


def test_invalidation_discards_inflight_generation_and_is_nonblocking():
    store = TrackingStore()
    started, release = threading.Event(), threading.Event()
    value = [1]

    def compute(shared):
        captured = value[0]
        started.set()
        assert release.wait(5)
        return {"value": captured}

    mat = ResponseMaterializer("s2p", lambda: store, {"summary": compute})
    with ThreadPoolExecutor() as pool:
        future = pool.submit(mat.refresh)
        assert started.wait(5)
        mat.invalidate()  # Would deadlock here if invalidation waited for IO.
        value[0] = 2
        release.set()
        future.result(timeout=5)
    assert mat.get("summary") is None
    assert mat.get_or_refresh("summary") == {"value": 2}
    assert store.reads == 2


def test_failed_source_can_retry_and_computation_failure_is_isolated():
    store = TrackingStore()
    attempts = [0]

    def provider():
        attempts[0] += 1
        if attempts[0] == 1:
            raise RuntimeError("offline")
        return store

    def broken(shared):
        raise ValueError("missing optional data")

    mat = ResponseMaterializer("s2p", provider, {"bad": broken, "good": lambda s: {"ok": True}})
    with pytest.raises(RuntimeError, match="offline"):
        mat.get_or_refresh("good")
    assert mat.get_or_refresh("good") == {"ok": True}
    assert mat.get("bad")["status"] == "unavailable"
    assert mat.get_or_refresh("unknown") is None
    assert store.reads == 1


def test_all_six_payloads_prewarm_without_observation_writes_and_keep_live_shapes(monkeypatch):
    from app.domains.s2p.config import S2PDomainConfig
    from app.routers import s2p_data_helpers, s2p_evidence, s2p_insight, s2p_performance, s2p_preview
    from app.routers.s2p import _read_conservation_counts

    store = TrackingStore()
    invoice = {"invoice_id": "materializer-invoice", "event_id": "materializer-event",
               "category": "price_variance", "factors": {"tax_regulatory_compliance": 0.1}}
    decision_id = store.write_decision("s2p", "price_variance", "auto_approve", 0.8, {},
                                     metadata={**invoice, "supplier_id": "supplier-a", "amount": 100})
    monkeypatch.setattr(s2p_data_helpers, "load_invoices", lambda: [invoice])
    monkeypatch.setattr(s2p_evidence, "_load_invoices", lambda: [invoice])
    monkeypatch.setattr(s2p_insight, "find_invoice", lambda identity: invoice if identity in
                        {invoice["invoice_id"], invoice["event_id"]} else None)
    app = FastAPI()
    app.state.graph_store = store
    app.state.scorer = SimpleNamespace(
        graph_store=store, version="test",
        score_read_only=lambda *args: SimpleNamespace(action="auto_approve", action_index=0,
            confidence=0.8, probabilities=[1 / len(S2PDomainConfig.actions)] * len(S2PDomainConfig.actions)),
    )
    for router in (s2p_preview.router, s2p_performance.router, s2p_evidence.router, s2p_insight.router):
        app.include_router(router)
    client = TestClient(app)
    paths = ["/api/s2p/preview/queue?limit=1", "/api/s2p/preview/suppliers?limit=1",
             "/api/s2p/performance/summary", "/api/s2p/evidence/compliance",
             "/api/s2p/insight/fingerprint?invoice_id=materializer-event"]
    s2p_preview.reset_preview_state()
    live = [client.get(path).json() for path in paths]
    mat = create_s2p_materializer(app.state)
    store.reads = store.verified_reads = store.observations = 0
    mat.refresh()
    assert store.observations == 0
    assert (store.reads, store.verified_reads) == (1, 1)
    for key in mat._computations:
        assert mat.get(key) is not None
        assert mat.get(key).get("status") != "unavailable", key
    app.state.materializer = mat
    assert [client.get(path).json() for path in paths] == live
    assert (store.reads, store.verified_reads) == (1, 1)
    counts = dict(_read_conservation_counts(store, "s2p"))
    counts.update(verified_decisions=[], recent_outcomes=[])
    assert mat.get("conservation_status") == conservation_payload(counts)
    assert client.get("/api/s2p/insight/fingerprint?invoice_id=missing").json()["error"]
    store.write_outcome(decision_id, "auto_approve", True, domain="s2p")
    mat.invalidate()
    assert client.get(paths[0]).json()["total"] == 0
    assert client.get(paths[2]).json()["total_verified"] == 1
    assert (store.reads, store.verified_reads) == (3, 3)
    from copilot_sdk.config.tenant import tenant_context
    with tenant_context("another-tenant"):
        assert mat.get("preview_queue") is None
        assert mat.get_or_refresh("preview_queue") is None
    assert (store.reads, store.verified_reads) == (3, 3)
    app.state.graph_store = TrackingStore()
    assert mat.get("preview_queue") is None
    s2p_preview.reset_preview_state()


@pytest.mark.asyncio
async def test_startup_prewarms_and_shutdown_stops_refresh_task(monkeypatch):
    from app import main

    store = TrackingStore()
    mat = ResponseMaterializer("s2p", lambda: store, {"ready": lambda s: {"ok": True}})

    async def warm():
        assert mat.get("ready") == {"ok": True}

    monkeypatch.setattr(main.app.state, "materializer", None, raising=False)
    monkeypatch.setattr(main.app.state, "materializer_task", None, raising=False)
    monkeypatch.setattr(main.app.state, "s2p_tab_state_cache", SimpleNamespace(warm_up=warm))
    monkeypatch.setattr(main, "create_s2p_materializer", lambda state: mat)
    monkeypatch.setattr(main.audit, "configure_graph_store", lambda store: None)
    monkeypatch.setattr(main, "_warm_s2p_learn_store_connections", lambda: None)
    monkeypatch.setattr(main, "warm_financial_snapshots", lambda store: None)
    monkeypatch.setattr(main, "warm_factor_snapshots", lambda scorer: None)
    await main.warm_s2p_tab_state_cache()
    task = main.app.state.materializer_task
    assert not task.done()
    assert (store.reads, store.verified_reads) == (1, 1)
    await main.stop_s2p_materializer()
    assert task.done()
    assert main.app.state.materializer is None
    assert mat.get("ready") is None
