from __future__ import annotations

import threading

import pytest

from app.services.graph_timeout import GraphTimeoutError, with_graph_timeout


def test_with_graph_timeout_returns_result() -> None:
    result = with_graph_timeout(lambda: 42, timeout=5.0, label="test")
    assert result == 42


def test_with_graph_timeout_raises_on_slow_call() -> None:
    hang = threading.Event()

    def slow() -> str:
        hang.wait(30)
        return "never"

    with pytest.raises(GraphTimeoutError, match="timed out"):
        with_graph_timeout(slow, timeout=1.0, label="slow_test")
    hang.set()


def test_with_graph_timeout_passes_args() -> None:
    def add(a: int, b: int) -> int:
        return a + b

    result = with_graph_timeout(add, 3, 4, timeout=5.0, label="add")
    assert result == 7


def test_with_graph_timeout_passes_kwargs() -> None:
    def greet(name: str = "world") -> str:
        return f"hello {name}"

    result = with_graph_timeout(greet, timeout=5.0, label="greet", name="test")
    assert result == "hello test"
