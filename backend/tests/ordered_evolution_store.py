"""Test graph store with causal ordering for automatically timestamped events."""

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from copilot_sdk.graph.memory_store import InMemoryGraphStore


class OrderedEvolutionGraphStore(InMemoryGraphStore):
    """Keep registrations, resets, and outcomes ordered even on a coarse clock."""

    def __init__(self, domain: str = "test", decision_id_prefix: str = "") -> None:
        super().__init__(domain=domain, decision_id_prefix=decision_id_prefix)
        self._event_clock = datetime.min.replace(tzinfo=timezone.utc)
        self._event_timestamps: dict[str, str] = {}
        self._event_clock_lock = RLock()

    def write_evolution_event(
        self,
        event_id: str,
        domain: str,
        event_type: str,
        rule_name: str,
        variant_id: str,
        source_copilot: str | None = None,
        source_rule: str | None = None,
        metric: float | None = None,
        shadow_batch_size: int | None = None,
        min_shadow_batches: int | None = None,
        metadata: dict[str, Any] | None = None,
        decision_id: str | None = None,
    ) -> None:
        with self._event_clock_lock:
            event_metadata = dict(metadata or {})
            timestamp = self._event_timestamps.get(event_id)
            if timestamp is None:
                self._event_clock = max(
                    datetime.now(timezone.utc),
                    self._event_clock + timedelta(microseconds=1),
                )
                timestamp = self._event_clock.isoformat()
            # Preserve deliberate timestamps; reuse generated ones for idempotent retries.
            event_metadata.setdefault("timestamp", timestamp)
            super().write_evolution_event(
                event_id=event_id,
                domain=domain,
                event_type=event_type,
                rule_name=rule_name,
                variant_id=variant_id,
                source_copilot=source_copilot,
                source_rule=source_rule,
                metric=metric,
                shadow_batch_size=shadow_batch_size,
                min_shadow_batches=min_shadow_batches,
                metadata=event_metadata,
                decision_id=decision_id,
            )
            self._event_timestamps[event_id] = timestamp
