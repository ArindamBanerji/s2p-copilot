"""App-owned response snapshots; synchronous callers share one refresh.

No dependency on the SDK materializer. Published payloads are owned by this
object, never by callers. Invalidation cannot publish an in-flight old build.
"""
from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from typing import Any, Callable

log = logging.getLogger(__name__)


class ResponseMaterializer:
    def __init__(
        self, domain: str, store_provider: Callable[[], Any],
        computations: dict[str, Callable[[dict[str, Any]], Any]], ttl: float = 30.0,
        *, available: Callable[[], bool] | None = None,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self._domain = domain
        self._store_provider = store_provider
        self._computations = dict(computations)
        self._materialized: dict[str, Any] = {}
        self._lock = threading.Condition(threading.RLock())
        self._last_refresh: float | None = None
        self._ttl = ttl
        self._refreshing = False
        self._generation = 0
        self._failure: BaseException | None = None
        self._available = available or (lambda: True)

    def refresh(self, *, if_stale: bool = False) -> None:
        if not self._available():
            return
        with self._lock:
            if if_stale and self._last_refresh is not None and time.monotonic() - self._last_refresh < self._ttl:
                return
            if self._refreshing:
                self._lock.wait_for(lambda: not self._refreshing)
                if self._failure is not None:
                    raise RuntimeError("Response refresh failed") from self._failure
                return
            self._refreshing = True
            self._failure = None
            generation = self._generation
        try:
            store = self._store_provider()
            decisions = store.get_all_decisions(self._domain)
            verified = store.get_verified_decisions(self._domain)
            shared = {"decisions": decisions, "verified": verified,
                      "store": store, "domain": self._domain}
            results: dict[str, Any] = {}
            for key, fn in self._computations.items():
                try:
                    results[key] = fn(shared)
                except Exception as exc:
                    log.exception("S2P materialization failed: %s", key)
                    results[key] = {"error": str(exc), "status": "unavailable"}
            with self._lock:
                if generation == self._generation and self._available():
                    self._materialized = results
                    self._last_refresh = time.monotonic()
        except BaseException as exc:
            with self._lock:
                self._failure = exc
            raise
        finally:
            with self._lock:
                self._refreshing = False
                self._lock.notify_all()

    def get(self, key: str, *, item: str | None = None) -> Any:
        if not self._available():
            return None
        with self._lock:
            if self._refreshing:
                result = self._materialized.get(key)
                if item is not None and isinstance(result, dict):
                    result = result.get(item)
                return deepcopy(result)
            if self._last_refresh is None or time.monotonic() - self._last_refresh >= self._ttl:
                return None
            result = self._materialized.get(key)
            if item is not None and isinstance(result, dict):
                result = result.get(item)
        return deepcopy(result)

    def get_or_refresh(self, key: str, *, item: str | None = None) -> Any:
        if key not in self._computations or not self._available():
            return None
        # Retry an invalidated build, but do not spin under continuous writes.
        for _ in range(3):
            with self._lock:
                fresh = self._last_refresh is not None and time.monotonic() - self._last_refresh < self._ttl
            if fresh:
                return self.get(key, item=item)
            self.refresh(if_stale=True)
        return self.get(key, item=item)

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._last_refresh = None
            self._materialized = {}

    def wait_idle(self) -> None:
        """Join an in-flight thread before application resources are closed."""
        with self._lock:
            self._lock.wait_for(lambda: not self._refreshing)


def materialized_response(request: Any, key: str, *, item: str | None = None) -> Any:
    """Keep the original live/error path when no usable snapshot exists."""
    mat = getattr(request.app.state, "materializer", None)
    if mat is None:
        return None
    try:
        result = mat.get_or_refresh(key, item=item)
    except Exception:
        log.exception("S2P response refresh failed; using live handler: %s", key)
        return None
    if isinstance(result, dict) and result.get("status") == "unavailable" and "error" in result:
        return None
    return result


def invalidate_materializer(request: Any) -> None:
    mat = getattr(request.app.state, "materializer", None)
    if mat is not None:
        mat.invalidate()
