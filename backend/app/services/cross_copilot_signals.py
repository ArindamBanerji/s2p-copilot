"""Cross-copilot signal consumer for S2P scoring context."""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from urllib.parse import quote

import httpx

DEFAULT_SIGNAL_ENDPOINT = "http://127.0.0.1:8020"
SIGNAL_TTL_DAYS = 7
OFFLINE_CACHE_SECONDS = 2.0
_OFFLINE_CACHE: dict[tuple[str, str], float] = {}
_OFFLINE_CACHE_LOCK = threading.RLock()


class CrossCopilotSignalConsumer:
    def __init__(self, signal_endpoint: str | None = None):
        self._endpoint = (signal_endpoint or os.environ.get("CROSS_COPILOT_SIGNAL_ENDPOINT") or DEFAULT_SIGNAL_ENDPOINT).rstrip("/")

    def fetch_supplier_signals(self, supplier_name: str) -> list[dict[str, Any]]:
        """Fetch active supplier reliability signals without failing S2P scoring."""

        if not supplier_name:
            return []
        cache_key = (self._endpoint, str(supplier_name).strip().lower())
        now = time.time()
        with _OFFLINE_CACHE_LOCK:
            if _OFFLINE_CACHE.get(cache_key, 0.0) > now:
                return []

        try:
            encoded = quote(str(supplier_name), safe="")
            resp = httpx.get(
                f"{self._endpoint}/api/purchasing/signals/supplier/{encoded}",
                timeout=float(os.environ.get("CROSS_COPILOT_SIGNAL_TIMEOUT", "0.2")),
            )
            if resp.status_code != 200:
                _cache_offline(cache_key)
                return []
            raw: Any = resp.json()
        except Exception:
            _cache_offline(cache_key)
            return []
        if not isinstance(raw, list):
            return []
        now = time.time()
        return [
            dict(signal)
            for signal in raw
            if isinstance(signal, dict)
            and str(signal.get("provenance") or "") == "signal"
            and _supplier_matches(signal, supplier_name)
            and not signal_expired(signal, now)
        ]


def _cache_offline_unlocked(cache_key: tuple[str, str]) -> None:
    _OFFLINE_CACHE[cache_key] = time.time() + OFFLINE_CACHE_SECONDS


def _cache_offline(cache_key: tuple[str, str]) -> None:
    with _OFFLINE_CACHE_LOCK:
        _cache_offline_unlocked(cache_key)


def latest_supplier_signal(signals: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not signals:
        return None
    return max(signals, key=lambda item: _float(item.get("timestamp"), 0.0))


def signal_expired(signal: dict[str, Any], now: float | None = None) -> bool:
    current = time.time() if now is None else float(now)
    timestamp = _float(signal.get("timestamp"), 0.0)
    ttl_days = int(_float(signal.get("ttl_days"), SIGNAL_TTL_DAYS))
    return current - timestamp >= ttl_days * 86400


def supplier_exception_from_reliability(reliability_pct: Any) -> float:
    reliability = max(0.0, min(_float(reliability_pct, 100.0), 100.0)) / 100.0
    return round(1.0 - reliability, 6)


def _supplier_matches(signal: dict[str, Any], supplier_name: str) -> bool:
    signal_supplier = str(signal.get("supplier_name") or "").strip().lower()
    if not signal_supplier:
        return True
    return signal_supplier == str(supplier_name or "").strip().lower()


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
