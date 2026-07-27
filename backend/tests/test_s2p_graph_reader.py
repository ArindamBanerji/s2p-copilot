from __future__ import annotations

from typing import Any

import pytest

from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader


class RecordingGraphStore:
    def __init__(self, *, fail_operation: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail_operation = fail_operation

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))
        if self.fail_operation == name:
            raise OSError(f"{name} unavailable")

    def get_decision(self, decision_id: str, domain: str | None = None) -> dict[str, Any] | None:
        self._record("get_decision", decision_id, domain=domain)
        return None

    def get_decisions(self, domain: str, category: str | None = None, limit: int = 400) -> list[dict[str, Any]]:
        self._record("get_decisions", domain, category=category, limit=limit)
        return []

    def get_all_decisions(self, domain: str) -> list[dict[str, Any]]:
        self._record("get_all_decisions", domain)
        return []

    def get_verified_decisions(self, domain: str) -> list[dict[str, Any]]:
        self._record("get_verified_decisions", domain)
        return []

    def count_verified(self, domain: str) -> int:
        self._record("count_verified", domain)
        return 0

    def count_verified_decisions(self, domain: str) -> int:
        self._record("count_verified_decisions", domain)
        return 0

    def count_correct(self, domain: str) -> int:
        self._record("count_correct", domain)
        return 0

    def count_decisions(self, domain: str) -> int:
        self._record("count_decisions", domain)
        return 0

    def get_decision_links(
        self,
        decision_id: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self._record("get_decision_links", decision_id, domain=domain, limit=limit)
        return []

    def query_context(
        self,
        entity_id: str,
        max_depth: int,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        self._record("query_context", entity_id, max_depth, domain=domain)
        return []


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
    assert reader.query_context("invoice-1", max_depth=4) == []

    assert store.calls == [
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
    ]


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


class StatefulGraphStore(RecordingGraphStore):
    def __init__(self) -> None:
        super().__init__()
        self.decisions = [
            {"decision_id": "D-1", "domain": "soc", "category": "invoice", "action": "reject"},
            {"decision_id": "D-1", "domain": "s2p", "category": "invoice", "action": "approve"},
        ]
        self.links = [
            {"decision_id": "D-1", "domain": "soc", "entity_id": "INV-1"},
            {"decision_id": "D-1", "domain": "s2p", "entity_id": "INV-1"},
        ]

    def get_decision(self, decision_id: str, domain: str | None = None) -> dict[str, Any] | None:
        self._record("get_decision", decision_id, domain=domain)
        return next(
            (dict(row) for row in self.decisions if row["decision_id"] == decision_id and row["domain"] == domain),
            None,
        )

    def get_all_decisions(self, domain: str) -> list[dict[str, Any]]:
        self._record("get_all_decisions", domain)
        return [dict(row) for row in self.decisions if row["domain"] == domain]

    def get_decision_links(
        self,
        decision_id: str | None = None,
        domain: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self._record("get_decision_links", decision_id, domain=domain, limit=limit)
        rows = [
            dict(row)
            for row in self.links
            if row["domain"] == domain
            and (decision_id is None or row["decision_id"] == decision_id)
        ]
        return rows if limit is None else rows[:limit]


def test_facade_isolates_s2p_from_same_id_soc_data() -> None:
    reader = S2PGraphReader(StatefulGraphStore())

    assert reader.get_decision("D-1")["domain"] == "s2p"
    assert [row["domain"] for row in reader.get_all_decisions()] == ["s2p"]
    assert [row["domain"] for row in reader.get_decision_links("D-1")] == ["s2p"]
    assert reader.count_recommended_action("approve") == 1
