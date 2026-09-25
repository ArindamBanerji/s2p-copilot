"""Persist observed S2P budget allocations, never replay allocation decisions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from threading import RLock
from typing import Any
from uuid import uuid4

from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.scoring.budget_policy import AdaptiveBudgetPolicy, MAX_HISTORY


STATE_KEY = "s2p:investigation-budget:v1"
_STATE_KEYS = (STATE_KEY + ":alternate", STATE_KEY)
_COUNTERS = (
    "_total_allocations", "_total_easy", "_total_hard", "_total_easy_reads",
    "_total_hard_reads", "_total_reads_saved",
)
_CONFIG = ("min_reads", "max_reads", "default_reads", "safety_lambda")


class PersistentBudgetPolicy(AdaptiveBudgetPolicy):
    """S2P adapter for the SDK's in-memory policy state (single writer process).

    The SDK exposes no hydration hook. Keep its private-state dependency here;
    snapshots preserve counters exactly, including history already evicted.
    Storage failures propagate and roll back local telemetry.

    One writer process owns these two snapshot slots. The GraphStore interface
    has no compare-and-swap; multiple workers would overwrite each other's
    counters. The lock synchronizes allocation and telemetry within this owner.
    """

    def __init__(self, store: GraphStore) -> None:
        super().__init__(safety_lambda=0.0)
        self._store = store
        self._lock = RLock()
        snapshots = []
        for key in _STATE_KEYS:
            state = store.get_governance("s2p", key)
            if state is None:
                continue
            self._validate(state)
            snapshots.append(state)
        if snapshots:
            self._restore(max(snapshots, key=lambda state: state["counters"]["_total_allocations"]))

    def _snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "domain": "s2p",
            "config": {name: getattr(self, name) for name in _CONFIG},
            "history": deepcopy(self._history),
            "counters": {name: getattr(self, name) for name in _COUNTERS},
        }

    def _restore(self, state: dict[str, Any]) -> None:
        self._history = deepcopy(state["history"][-MAX_HISTORY:])
        for name in _COUNTERS:
            setattr(self, name, state["counters"][name])

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(super().get_stats())

    def telemetry_snapshot(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return counters and detached allocation rows from the same instant."""
        with self._lock:
            return self.get_stats(), deepcopy(self._history)

    def _validate(self, state: dict[str, Any]) -> None:
        if (state.get("version") != 1 or state.get("domain") != "s2p"
                or state.get("config") != self._snapshot()["config"]):
            raise ValueError("Incompatible S2P budget snapshot")
        counters, history = state.get("counters"), state.get("history")
        if not isinstance(counters, dict) or not isinstance(history, list):
            raise ValueError("Malformed S2P budget snapshot")
        for name in _COUNTERS:
            value = counters.get(name)
            if type(value) is not int or (name != "_total_reads_saved" and value < 0):
                raise ValueError("Malformed S2P budget counters")
        total = counters["_total_allocations"]
        easy, hard = counters["_total_easy"], counters["_total_hard"]
        if (len(history) != min(total, MAX_HISTORY)
                or easy + hard > total
                or counters["_total_easy_reads"] != easy * self.min_reads
                or counters["_total_hard_reads"] != hard * self.max_reads
                or counters["_total_reads_saved"] != (
                    easy * (self.default_reads - self.min_reads)
                    + hard * (self.default_reads - self.max_reads))):
            raise ValueError("Inconsistent S2P budget history")
        visible_easy = visible_hard = 0
        for row in history:
            if not isinstance(row, dict):
                raise ValueError("Malformed S2P budget allocation")
            confidence = row.get("confidence")
            if (not isinstance(confidence, (float, int)) or isinstance(confidence, bool)
                    or not isfinite(confidence) or not 0 <= confidence <= 1
                    or type(row.get("reads_allocated")) is not int
                    or not self.min_reads <= row["reads_allocated"] <= self.max_reads
                    or row["reads_allocated"] not in {self.min_reads, self.default_reads, self.max_reads}
                    or not isinstance(row.get("category"), str)
                    or not isinstance(row.get("allocation_id"), str)
                    or not isinstance(row.get("recorded_at"), str)
                    or not isinstance(row.get("provenance"), dict)
                    or (row.get("decision_id") is not None
                        and not isinstance(row["decision_id"], str))):
                raise ValueError("Malformed S2P budget allocation")
            visible_easy += row["reads_allocated"] == self.min_reads
            visible_hard += row["reads_allocated"] == self.max_reads
        if (visible_easy > easy or visible_hard > hard
                or len(history) - visible_easy - visible_hard > total - easy - hard):
            raise ValueError("S2P budget history exceeds its counters")

    def allocate(
        self, confidence: float, category: str, verified_count: int,
        *, decision_id: str | None = None,
    ) -> int:
        if not isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Budget confidence must be finite and between zero and one")
        with self._lock:
            before = self._snapshot()
            reads = int(super().allocate(confidence, category, verified_count))
            if self._total_allocations == before["counters"]["_total_allocations"]:
                return reads  # SDK cold start emits no measured allocation.
            try:
                decision = (self._store.get_decision(decision_id, domain="s2p")
                            if decision_id else None)
                provenance: dict[str, Any] = {
                    "source": "adaptive_budget_policy.allocate",
                    "measurement": "allocated_reads",
                    "decision_found": decision is not None,
                }
                # Preserve sample/planted labels without turning a request ID
                # into a claim of verified attribution or customer evidence.
                for name, source in (("decision", decision),
                                     ("metadata", (decision or {}).get("metadata")),
                                     ("context", (decision or {}).get("context"))):
                    if isinstance(source, dict):
                        provenance[name] = {
                            key: deepcopy(source[key])
                            for key in ("provenance", "planted", "source", "evidence_tier")
                            if key in source
                        }
                self._history[-1].update({
                    "decision_id": decision_id,
                    "allocation_id": uuid4().hex,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "verified_count": verified_count,
                    "provenance": provenance,
                })
                # AGE replaces a governance node using delete then create.
                # Alternate slots so interruption cannot delete the last
                # completed snapshot. Each slot holds at most MAX_HISTORY rows.
                key = _STATE_KEYS[self._total_allocations % len(_STATE_KEYS)]
                self._store.save_governance("s2p", key, self._snapshot())
            except Exception:
                self._restore(before)
                raise
            return reads
