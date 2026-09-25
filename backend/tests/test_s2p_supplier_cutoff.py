from __future__ import annotations

from app.services.s2p_context_builder import S2PContextBuilder


class FakeReader:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def get_decisions(self, category: str, limit: int = 400) -> list[dict]:
        return [row for row in self.rows if row.get("category") == category][:limit]


def _row(decision_id: str, timestamp: str, action: str = "accept_with_adjustment") -> dict:
    return {
        "decision_id": decision_id,
        "category": "quantity_mismatch",
        "supplier_id": "SUP-ASTER",
        "actual_action": action,
        "created_at": timestamp,
        "metadata": {
            "supplier_id": "SUP-ASTER",
            "category": "quantity_mismatch",
            "created_at": timestamp,
        },
    }


def _builder(rows: list[dict]) -> S2PContextBuilder:
    return S2PContextBuilder(reader=FakeReader(rows), graph_store=object())


def test_supplier_cutoff_excludes_future() -> None:
    builder = _builder(
        [
            _row("D-1", "2026-01-01T00:00:00+00:00"),
            _row("D-2", "2026-01-02T00:00:00+00:00"),
            _row("D-3", "2026-01-03T00:00:00+00:00"),
        ]
    )

    matches = builder.find_similar_decisions(
        supplier_id="SUP-ASTER",
        category="quantity_mismatch",
        as_of_date="2026-01-02T00:00:00+00:00",
        max_results=10,
    )

    assert {row["decision_id"] for row in matches} == {"D-1", "D-2"}


def test_supplier_cutoff_none_includes_all() -> None:
    builder = _builder(
        [
            _row("D-1", "2026-01-01T00:00:00+00:00"),
            _row("D-2", "2026-01-02T00:00:00+00:00"),
            _row("D-3", "2026-01-03T00:00:00+00:00"),
        ]
    )

    matches = builder.find_similar_decisions(
        supplier_id="SUP-ASTER",
        category="quantity_mismatch",
        max_results=10,
    )

    assert {row["decision_id"] for row in matches} == {"D-1", "D-2", "D-3"}


def test_supplier_ratio_changes_with_cutoff() -> None:
    rows = [
        _row("D-1", "2026-01-01T00:00:00+00:00", "accept_with_adjustment"),
        _row("D-2", "2026-01-02T00:00:00+00:00", "accept_with_adjustment"),
        _row("D-3", "2026-01-03T00:00:00+00:00", "flag_leakage"),
    ]
    builder = _builder(rows)

    before_future = builder.find_similar_decisions(
        supplier_id="SUP-ASTER",
        category="quantity_mismatch",
        as_of_date="2026-01-02T12:00:00+00:00",
        max_results=10,
    )
    all_rows = builder.find_similar_decisions(
        supplier_id="SUP-ASTER",
        category="quantity_mismatch",
        max_results=10,
    )

    before_ratio = sum(row["actual_action"] == "accept_with_adjustment" for row in before_future) / len(before_future)
    all_ratio = sum(row["actual_action"] == "accept_with_adjustment" for row in all_rows) / len(all_rows)
    assert before_ratio == 1.0
    assert all_ratio < before_ratio


def test_cutoff_in_investigation_context_uses_episode_timestamp() -> None:
    builder = _builder(
        [
            _row("D-1", "2026-01-01T00:00:00+00:00"),
            _row("D-2", "2026-01-03T00:00:00+00:00"),
        ]
    )

    result = builder.build_invoice_context(
        invoice_id="INV-CUTOFF",
        category="quantity_mismatch",
        decision_id=None,
        context_data={
            "invoice_id": "INV-CUTOFF",
            "supplier_id": "SUP-ASTER",
            "category": "quantity_mismatch",
            "episode_timestamp": "2026-01-02T00:00:00+00:00",
        },
    )

    similar = [node for node in result.nodes if node.type == "similar_decision"]
    assert [node.properties["decision_id"] for node in similar] == ["D-1"]
    assert result.metadata["similarity_criteria"]["as_of_date"] == "2026-01-02T00:00:00+00:00"
