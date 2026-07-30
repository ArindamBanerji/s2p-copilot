import asyncio
from typing import Any

import pytest

from app.framework.similar_cases_base import SimilarCasesBase
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader


class Finder(SimilarCasesBase):
    def get_theta(self, category: str) -> float:
        return 0.0


class ReaderDouble(S2PGraphReader):
    domain = "s2p"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        # The test double implements the facade method directly; no raw store
        # or runtime interface probing is involved.
        self.rows = rows
        self.calls = 0

    def get_verified_decisions(self) -> list[dict[str, Any]]:
        self.calls += 1
        return list(self.rows)


class FailingReader(ReaderDouble):
    def get_verified_decisions(self) -> list[dict[str, Any]]:
        raise GraphUnavailableError("graph unavailable")


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "decision_id": f"d-{index}",
            "category": "price_variance",
            "action": "approve",
            "confidence": 0.9,
            "outcome": "approved",
            "factor_vector": [1.0, 0.0],
            "timestamp": index,
        }
        for index in range(5)
    ]


def test_similar_cases_uses_domain_bound_reader() -> None:
    reader = ReaderDouble(_rows())

    results = asyncio.run(
        Finder().get_similar_cases(
            factor_vector=[1.0, 0.0],
            category="price_variance",
            graph_reader=reader,
            k=1,
        )
    )

    assert reader.calls == 1
    assert len(results) == 1
    assert results[0]["decision_id"] == "d-0"


def test_similar_cases_propagates_reader_failure() -> None:
    with pytest.raises(GraphUnavailableError):
        asyncio.run(
            Finder().get_similar_cases(
                factor_vector=[1.0, 0.0],
                category="price_variance",
                graph_reader=FailingReader([]),
            )
        )
