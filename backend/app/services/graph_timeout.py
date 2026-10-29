"""Timeout wrapper for synchronous graph store calls."""
from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

_FALLBACK_TIMEOUT = 10.0  # seconds


class GraphTimeoutError(Exception):
    """A graph store call exceeded its time budget."""


def with_graph_timeout(
    fn: Callable[..., T],
    *args: Any,
    timeout: float = _FALLBACK_TIMEOUT,
    label: str = "graph_call",
    **kwargs: Any,
) -> T:
    """Run *fn* in a worker thread; raise GraphTimeoutError on timeout.

    Uses explicit shutdown(wait=False) so the worker can finish in the
    background without blocking the caller -- the same pattern as
    ResponseMaterializer.refresh().
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn, *args, **kwargs)
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        log.warning("Graph call %r timed out after %.1fs", label, timeout)
        raise GraphTimeoutError(
            f"Graph call {label!r} timed out after {timeout}s"
        ) from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
