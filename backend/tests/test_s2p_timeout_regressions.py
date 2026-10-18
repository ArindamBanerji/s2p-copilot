"""Regression coverage for the S2P tab timeout fixes."""

from __future__ import annotations

import threading
from time import monotonic
from types import SimpleNamespace
from typing import Any

from copilot_sdk.graph.memory_store import InMemoryGraphStore
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import s2p_evidence, s2p_governance, s2p_performance, s2p_pvg
from app.services.s2p_materialization import create_s2p_materializer


class _CachedMaterializer:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads

    def get_or_refresh(self, key: str, *, item: str | None = None) -> Any:
        result: Any = self.payloads.get(key)
        return result.get(item) if item is not None and isinstance(result, dict) else result


def _app_with_router(router: Any, payloads: dict[str, dict[str, Any]] | None = None) -> FastAPI:
    app = FastAPI()
    if payloads is not None:
        app.state.materializer = _CachedMaterializer(payloads)
    app.include_router(router)
    return app


def test_audit_pack_uses_materialized_snapshot(monkeypatch: Any) -> None:
    payload = {
        "export_timestamp": "2026-10-14T00:00:00+00:00",
        "receipt_count": 0,
        "chain_integrity": {"verified": True},
        "conservation_state": {},
        "override_distribution": {},
        "override_count": 0,
        "confirm_count": 0,
        "receipts": [],
    }
    monkeypatch.setattr(
        s2p_evidence,
        "get_receipt_store",
        lambda: (_ for _ in ()).throw(AssertionError("live receipt read")),
    )

    app = _app_with_router(s2p_evidence.router, {"audit_pack": payload})
    with TestClient(app) as client:
        response = client.get("/api/s2p/evidence/audit-pack")

    assert response.status_code == 200
    assert response.json() == payload


def test_compliance_screening_uses_materialized_snapshot(monkeypatch: Any) -> None:
    payload = {"total_decisions_screened": 0, "compliance_rate": 1.0}
    monkeypatch.setattr(
        s2p_governance,
        "_screen_receipts",
        lambda request: (_ for _ in ()).throw(AssertionError("live screening")),
    )

    app = _app_with_router(
        s2p_governance.router,
        {"governance_compliance_screening": payload},
    )
    with TestClient(app) as client:
        response = client.get("/api/s2p/governance/compliance-screening")

    assert response.status_code == 200
    assert response.json() == payload


def test_live_compliance_screening_caps_receipt_scan(monkeypatch: Any) -> None:
    class Store:
        requested_limit: int | None = None

        def get_chain(self, limit: int) -> list[dict[str, Any]]:
            self.requested_limit = limit
            return []

        def verify_chain(self) -> dict[str, Any]:
            return {"verified": True}

        @property
        def stats(self) -> dict[str, Any]:
            return {"overrides": 0, "confirms": 0}

    store = Store()
    monkeypatch.setattr(s2p_governance, "get_receipt_store", lambda: store)
    monkeypatch.setattr(
        s2p_governance,
        "_safe_conservation_snapshot",
        lambda request: {"state": "GREEN"},
    )

    result = s2p_governance._screen_receipts(SimpleNamespace())

    assert store.requested_limit == s2p_governance.SCREENING_RECEIPT_LIMIT == 500
    assert result["total_decisions_screened"] == 0


def test_materializer_prewarms_timeout_payloads_past_refresh_interval() -> None:
    store = InMemoryGraphStore(domain="s2p")
    state = SimpleNamespace(graph_store=store, scorer=SimpleNamespace(graph_store=store))

    materializer = create_s2p_materializer(state)

    assert {"audit_pack", "governance_compliance_screening", "performance_summary"} <= set(
        materializer._computations
    )
    assert materializer._ttl > 30.0


def test_pvg_leakage_factor_error_returns_503(monkeypatch: Any) -> None:
    monkeypatch.setattr(s2p_pvg, "load_invoices", lambda: [{"invoice_id": "INV-1"}])
    monkeypatch.setattr(
        s2p_pvg,
        "compute_all_factors",
        lambda invoice: (_ for _ in ()).throw(ValueError("context absent")),
    )

    with TestClient(_app_with_router(s2p_pvg.router)) as client:
        response = client.get("/api/s2p/pvg/leakage")

    assert response.status_code == 503
    assert "factor context unavailable" in response.json()["detail"]


def _assert_deadline_returns_503(
    monkeypatch: Any,
    *,
    module: Any,
    timeout_name: str,
    router: Any,
    path: str,
    unavailable_text: str,
) -> None:
    block = threading.Event()
    started = threading.Event()
    finished = threading.Event()

    def slow_materialized_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started.set()
        try:
            block.wait(timeout=10)
            return {}
        finally:
            finished.set()

    monkeypatch.setattr(module, "materialized_response", slow_materialized_response)
    monkeypatch.setattr(module, timeout_name, 0.05)
    app = _app_with_router(router)
    with TestClient(app) as client:
        started_at = monotonic()
        try:
            response = client.get(path)
            elapsed = monotonic() - started_at
            assert response.status_code == 503
            assert unavailable_text in response.json()["detail"]
            assert elapsed < 5.0
            assert started.wait(timeout=1)
        finally:
            block.set()
        assert finished.wait(timeout=2)


def test_audit_pack_deadline_returns_503(monkeypatch: Any) -> None:
    _assert_deadline_returns_503(
        monkeypatch,
        module=s2p_evidence,
        timeout_name="AUDIT_PACK_TIMEOUT_SECONDS",
        router=s2p_evidence.router,
        path="/api/s2p/evidence/audit-pack",
        unavailable_text="unavailable",
    )


def test_compliance_screening_deadline_returns_503(monkeypatch: Any) -> None:
    _assert_deadline_returns_503(
        monkeypatch,
        module=s2p_governance,
        timeout_name="COMPLIANCE_SCREENING_TIMEOUT_SECONDS",
        router=s2p_governance.router,
        path="/api/s2p/governance/compliance-screening",
        unavailable_text="unavailable",
    )


def test_performance_summary_deadline_returns_503(monkeypatch: Any) -> None:
    _assert_deadline_returns_503(
        monkeypatch,
        module=s2p_performance,
        timeout_name="PERFORMANCE_SUMMARY_TIMEOUT_SECONDS",
        router=s2p_performance.router,
        path="/api/s2p/performance/summary",
        unavailable_text="unavailable",
    )
