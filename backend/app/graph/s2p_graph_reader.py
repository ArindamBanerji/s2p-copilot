"""Domain-bound read facade for S2P graph access."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

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
        return cast(dict[str, Any] | None, self._read(
            "get_decision",
            lambda: self.store.get_decision(decision_id, domain=self.domain),
        ))

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
        count_fn = getattr(self.store, "count_verified", None)
        if not callable(count_fn):
            return 0
        count_verified = cast(Callable[[str], Any], count_fn)
        return cast(int, self._read(
            "count_verified",
            lambda: count_verified(self.domain),
        ))

    def count_verified_decisions(self) -> int:
        count_fn = getattr(self.store, "count_verified_decisions", None)
        if not callable(count_fn):
            return 0
        count_verified_decisions = cast(Callable[[str], Any], count_fn)
        return cast(int, self._read(
            "count_verified_decisions",
            lambda: count_verified_decisions(self.domain),
        ))

    def count_correct(self) -> int:
        count_fn = getattr(self.store, "count_correct", None)
        if not callable(count_fn):
            return 0
        count_correct = cast(Callable[[str], Any], count_fn)
        return cast(int, self._read(
            "count_correct",
            lambda: count_correct(self.domain),
        ))

    def count_decisions(self) -> int:
        return cast(int, self._read(
            "count_decisions",
            lambda: self.store.count_decisions(self.domain),
        ))

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

    def _age_store(self) -> Any:
        """Return the concrete AGE store behind S2P's adapter chain.

        The directed reads are deliberately S2P-local.  They use the
        concrete AGE query runner because GraphStore.query_context is a
        shared generic API and must retain its existing semantics.
        """
        store: Any = self.store
        for _ in range(2):
            inner = getattr(store, "_store", None)
            if inner is None:
                break
            store = inner
        if not callable(getattr(store, "_run_query", None)):
            raise GraphUnavailableError("S2P AGE store does not expose directed query support")
        return store

    @staticmethod
    def _normalize_vertex(store: Any, row: dict[str, Any], key: str) -> dict[str, Any]:
        raw = row.get(key, row)
        converter = getattr(store, "_node_to_dict", None)
        node = converter(raw) if callable(converter) else raw
        if not isinstance(node, dict):
            node = {}
        if not node.get("_label"):
            label_by_key = (
                ("gr_id", "GoodsReceipt"),
                ("po_id", "PurchaseOrder"),
                ("contract_id", "Contract"),
                ("commodity_id", "Commodity"),
                ("supplier_id", "Supplier"),
                ("invoice_id", "Invoice"),
            )
            for identity_key, label in label_by_key:
                if node.get(identity_key) is not None:
                    node["_label"] = label
                    break
        return {"node": node}

    def query_direct_context(
        self,
        invoice_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read only the directed Invoice -> entity neighbors for S2P."""
        bounded_limit = max(1, min(int(limit), 100))

        try:
            self._age_store()
        except GraphUnavailableError:
            query_context = getattr(self.store, "query_context", None)
            if not callable(query_context):
                raise GraphUnavailableError("S2P AGE store does not expose directed query support")
            context_reader = cast(Callable[..., Any], query_context)
            return self._read(
                "query_direct_context",
                lambda: context_reader(invoice_id, 2, domain=self.domain),
            )

        def read() -> list[dict[str, Any]]:
            store = self._age_store()
            cypher_id = store._S(invoice_id)
            query = (
                f"MATCH (e:Invoice {{invoice_id: {cypher_id}}})-[]->(n) "
                f"WHERE n.domain = {store._S(self.domain)} "
                f"RETURN n LIMIT {bounded_limit}"
            )
            rows = store._run_query(query)
            normalized = [
                self._normalize_vertex(store, row, "n")
                for row in rows
                if isinstance(row, dict)
            ]
            allowed = {"PurchaseOrder", "GoodsReceipt", "Supplier", "Commodity", "Contract"}
            return [
                row for row in normalized
                if row["node"].get("_label") in allowed
            ]

        return self._read("query_direct_context", read)

    def query_duplicate_context(
        self,
        invoice_id: str,
        supplier_id: str,
        amount: float,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Read bounded, same-supplier near-amount invoice siblings."""
        bounded_limit = max(1, min(int(limit), 20))
        numeric_amount = float(amount)
        tolerance = abs(numeric_amount) * 0.05
        lower = max(0.0, numeric_amount - tolerance)
        upper = numeric_amount + tolerance

        try:
            self._age_store()
        except GraphUnavailableError:
            return []

        def read() -> list[dict[str, Any]]:
            store = self._age_store()
            query = (
                f"MATCH (:Supplier {{entity_id: {store._S(supplier_id)}}})"
                "<-[:SUPPLIED_BY]-(sib:Invoice) "
                f"WHERE sib.domain = {store._S(self.domain)} "
                f"AND sib.invoice_id <> {store._S(invoice_id)} "
                f"AND sib.amount > {lower} AND sib.amount < {upper} "
                f"RETURN sib LIMIT {bounded_limit}"
            )
            rows = store._run_query(query)
            return [
                self._normalize_vertex(store, row, "sib")
                for row in rows
                if isinstance(row, dict)
            ]

        return self._read("query_duplicate_context", read)
