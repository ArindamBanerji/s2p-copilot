"""Domain-bound read facade for S2P graph access."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from copilot_sdk.graph.protocol import GraphStore


T = TypeVar("T")


class GraphUnavailableError(RuntimeError):
    """Raised when an S2P graph read cannot be completed."""


class S2PGraphReader:
    """Expose canonical, domain-bound Decision reads for S2P callers."""

    def __init__(self, store: GraphStore, domain: str = "s2p") -> None:
        if domain != "s2p":
            raise ValueError("S2PGraphReader only supports domain='s2p'")
        self.store = store
        self.domain = domain

    def _read(self, operation: str, call: Callable[[], T]) -> T:
        try:
            return call()
        except GraphUnavailableError:
            raise
        except Exception as exc:
            raise GraphUnavailableError(f"S2P graph read failed: {operation}") from exc

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        return self._read(
            "get_decision",
            lambda: self.store.get_decision(decision_id, domain=self.domain),
        )

    def get_decisions(
        self,
        category: str | None = None,
        limit: int = 400,
    ) -> list[dict[str, Any]]:
        return self._read(
            "get_decisions",
            lambda: self.store.get_decisions(
                self.domain,
                category=category,
                limit=limit,
            ),
        )

    def get_all_decisions(self) -> list[dict[str, Any]]:
        return self._read(
            "get_all_decisions",
            lambda: self.store.get_all_decisions(self.domain),
        )

    def get_verified_decisions(self) -> list[dict[str, Any]]:
        return self._read(
            "get_verified_decisions",
            lambda: self.store.get_verified_decisions(self.domain),
        )

    def count_verified(self) -> int:
        return self._read(
            "count_verified",
            lambda: self.store.count_verified(self.domain),
        )

    def count_verified_decisions(self) -> int:
        return self._read(
            "count_verified_decisions",
            lambda: self.store.count_verified_decisions(self.domain),
        )

    def count_correct(self) -> int:
        return self._read(
            "count_correct",
            lambda: self.store.count_correct(self.domain),
        )

    def count_decisions(self) -> int:
        return self._read(
            "count_decisions",
            lambda: self.store.count_decisions(self.domain),
        )

    def count_recommended_action(self, action: str) -> int:
        """Count one action from the canonical S2P Decision read path."""
        def count() -> int:
            rows = self.store.get_all_decisions(self.domain)
            return sum(
                1
                for row in rows
                if isinstance(row, dict)
                and (row.get("recommended_action") or row.get("action")) == action
            )

        return self._read("count_recommended_action", count)

    def get_decision_links(
        self,
        decision_id: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._read(
            "get_decision_links",
            lambda: self.store.get_decision_links(
                decision_id=decision_id,
                domain=self.domain,
                limit=limit,
            ),
        )

    def query_context(
        self,
        entity_id: str,
        max_depth: int = 2,
    ) -> list[dict[str, Any]]:
        return self._read(
            "query_context",
            lambda: self.store.query_context(
                entity_id,
                max_depth,
                domain=self.domain,
            ),
        )
