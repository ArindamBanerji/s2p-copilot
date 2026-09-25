from __future__ import annotations

from typing import Any

import pytest

from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from copilot_sdk.graph.memory_store import InMemoryGraphStore


class RecordingGraphStore(InMemoryGraphStore):
    def __init__(self, *, fail_operation: str | None = None) -> None:
        super().__init__(domain="s2p")
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail_operation = fail_operation

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))
        if self.fail_operation == name:
            raise OSError(f"{name} unavailable")

    def get_decision(self, decision_id: str, domain: str | None = None) -> dict[str, Any] | None:
        self._record("get_decision", decision_id, domain=domain)
        return super().get_decision(decision_id, domain=domain or self.domain)

    def get_decisions(self, domain: str, category: str | None = None, limit: int = 400) -> list[dict[str, Any]]:
        self._record("get_decisions", domain, category=category, limit=limit)
        return super().get_decisions(domain, category=category, limit=limit)

    def get_all_decisions(self, domain: str) -> list[dict[str, Any]]:
        self._record("get_all_decisions", domain)
        return super().get_all_decisions(domain)

    def get_verified_decisions(self, domain: str) -> list[dict[str, Any]]:
        self._record("get_verified_decisions", domain)
        return super().get_verified_decisions(domain)

    def count_verified(self, domain: str) -> int:
        self._record("count_verified", domain)
        return 0

    def count_verified_decisions(self, domain: str) -> int:
        self._record("count_verified_decisions", domain)
        return super().count_verified_decisions(domain)

    def count_correct(self, domain: str) -> int:
        self._record("count_correct", domain)
        return super().count_correct(domain)

    def count_decisions(self, domain: str) -> int:
        self._record("count_decisions", domain)
        return super().count_decisions(domain)

    def get_decision_links(
        self,
        decision_id: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self._record("get_decision_links", decision_id, domain=domain, limit=limit)
        return super().get_decision_links(decision_id=decision_id, domain=domain, limit=limit)

    def query_context(
        self,
        entity_id: str,
        max_depth: int,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        self._record("query_context", entity_id, max_depth, domain=domain)
        return super().query_context(entity_id, max_depth, domain=domain)


def test_every_facade_method_injects_s2p_domain() -> None:
    store = RecordingGraphStore()
    reader = S2PGraphReader(store)

    assert reader.get_decision("D-1") is None
    assert reader.get_decisions("invoice", limit=7) == []
    assert reader.get_all_decisions() == []
    assert reader.get_verified_decisions() == []
    assert reader.count_verified() == 0
    assert reader.count_verified_decisions() == 0
    assert reader.count_correct() == 0
    assert reader.count_decisions() == 0
    assert reader.count_recommended_action("approve") == 0
    assert reader.get_decision_links("D-1", limit=3) == []
    context = reader.query_context("invoice-1", max_depth=4)
    assert isinstance(context, list)

    for expected in [
        ("get_decision", ("D-1",), {"domain": "s2p"}),
        ("get_decisions", ("s2p",), {"category": "invoice", "limit": 7}),
        ("get_all_decisions", ("s2p",), {}),
        ("get_verified_decisions", ("s2p",), {}),
        ("count_verified", ("s2p",), {}),
        ("count_verified_decisions", ("s2p",), {}),
        ("count_correct", ("s2p",), {}),
        ("count_decisions", ("s2p",), {}),
        ("get_all_decisions", ("s2p",), {}),
        ("get_decision_links", ("D-1",), {"domain": "s2p", "limit": 3}),
        ("query_context", ("invoice-1", 4), {"domain": "s2p"}),
    ]:
        assert expected in store.calls


def test_graph_failure_is_wrapped_with_chained_cause() -> None:
    reader = S2PGraphReader(RecordingGraphStore(fail_operation="get_decision"))

    with pytest.raises(GraphUnavailableError) as raised:
        reader.get_decision("D-1")

    assert isinstance(raised.value.__cause__, OSError)
    assert "get_decision" in str(raised.value)


def test_valid_empty_and_not_found_results_are_preserved() -> None:
    store = RecordingGraphStore()
    reader = S2PGraphReader(store)

    assert reader.get_decision("missing") is None
    assert reader.get_all_decisions() == []
    assert reader.get_decision_links() == []


def test_constructor_rejects_non_s2p_domain() -> None:
    with pytest.raises(ValueError, match="domain='s2p'"):
        S2PGraphReader(RecordingGraphStore(), domain="soc")


def _stateful_store() -> RecordingGraphStore:
    store = RecordingGraphStore()
    store.write_decision(
        "s2p",
        "invoice",
        "approve",
        0.9,
        {"match_status": 0.9},
        metadata={"decision_id": "D-1", "invoice_id": "INV-1"},
    )
    store.link_decision_to_entity("D-1", "INV-1", domain="s2p")
    store.calls.clear()
    return store


def test_facade_isolates_s2p_from_same_id_soc_data() -> None:
    reader = S2PGraphReader(_stateful_store())

    assert reader.get_decision("D-1")["domain"] == "s2p"
    assert [row["domain"] for row in reader.get_all_decisions()] == ["s2p"]
    links = reader.get_decision_links("D-1")
    assert len(links) == 1
    assert links[0]["decision_id"] == "D-1"
    assert links[0]["entity_id"] == "INV-1"
    assert reader.count_recommended_action("approve") == 1
