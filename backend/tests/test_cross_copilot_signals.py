from __future__ import annotations

import time

from app.services.cross_copilot_signals import (
    CrossCopilotSignalConsumer,
    latest_supplier_signal,
    signal_expired,
    supplier_exception_from_reliability,
)


class Response:
    def __init__(self, status_code=200, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("malformed")
        return self._payload


def signal(**overrides):
    payload = {
        "supplier_name": "Sysco",
        "reliability_pct": 74.0,
        "previous_pct": 93.0,
        "delta": -19.0,
        "trend": "declining",
        "source_copilot": "purchasing",
        "target_copilot": "s2p",
        "timestamp": time.time(),
        "ttl_days": 7,
        "provenance": "signal",
    }
    payload.update(overrides)
    return payload


def test_consumer_returns_signals_from_valid_endpoint(monkeypatch):
    monkeypatch.setattr(
        "app.services.cross_copilot_signals.httpx.get",
        lambda *args, **kwargs: Response(payload=[signal()]),
    )

    rows = CrossCopilotSignalConsumer("http://test").fetch_supplier_signals("Sysco")

    assert len(rows) == 1
    assert rows[0]["provenance"] == "signal"


def test_consumer_matches_supplier_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        "app.services.cross_copilot_signals.httpx.get",
        lambda *args, **kwargs: Response(payload=[signal(supplier_name="SYSCO")]),
    )

    rows = CrossCopilotSignalConsumer("http://test").fetch_supplier_signals("sysco")

    assert len(rows) == 1
    assert rows[0]["supplier_name"] == "SYSCO"


def test_consumer_filters_mismatched_supplier(monkeypatch):
    monkeypatch.setattr(
        "app.services.cross_copilot_signals.httpx.get",
        lambda *args, **kwargs: Response(payload=[signal(supplier_name="Other Supplier")]),
    )

    assert CrossCopilotSignalConsumer("http://test").fetch_supplier_signals("Sysco") == []


def test_consumer_returns_empty_when_purchasing_offline(monkeypatch):
    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr("app.services.cross_copilot_signals.httpx.get", offline)

    assert CrossCopilotSignalConsumer("http://test").fetch_supplier_signals("Sysco") == []


def test_consumer_filters_expired_signals(monkeypatch):
    monkeypatch.setattr(
        "app.services.cross_copilot_signals.httpx.get",
        lambda *args, **kwargs: Response(payload=[signal(timestamp=time.time() - 8 * 86400)]),
    )

    assert CrossCopilotSignalConsumer("http://test").fetch_supplier_signals("Sysco") == []


def test_consumer_returns_empty_for_no_signal_supplier(monkeypatch):
    monkeypatch.setattr(
        "app.services.cross_copilot_signals.httpx.get",
        lambda *args, **kwargs: Response(payload=[]),
    )

    assert CrossCopilotSignalConsumer("http://test").fetch_supplier_signals("Unknown") == []


def test_consumer_does_not_crash_on_malformed_response(monkeypatch):
    monkeypatch.setattr(
        "app.services.cross_copilot_signals.httpx.get",
        lambda *args, **kwargs: Response(payload=None, raises=True),
    )

    assert CrossCopilotSignalConsumer("http://test").fetch_supplier_signals("Sysco") == []


def test_signal_expiry_uses_ttl_days():
    assert signal_expired(signal(timestamp=time.time() - 8 * 86400))
    assert not signal_expired(signal(timestamp=time.time() - 6 * 86400))


def test_latest_supplier_signal_selects_newest():
    older = signal(timestamp=100.0, reliability_pct=80.0)
    newer = signal(timestamp=200.0, reliability_pct=70.0)

    assert latest_supplier_signal([older, newer]) == newer


def test_supplier_exception_maps_from_reliability():
    assert supplier_exception_from_reliability(74.0) == 0.26
