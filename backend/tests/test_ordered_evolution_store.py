"""Replay must remain causal when the system clock does not advance."""

from datetime import datetime, timezone, tzinfo
from unittest.mock import patch

import pytest

from app.domains.s2p.evolver_config import S2P_EVOLVER_CONFIG
from app.services import s2p_evolver
from tests.conftest import S2PTestGraphStore
from tests.ordered_evolution_store import OrderedEvolutionGraphStore
from tests.test_s2p_evolver import EvolverGraphStore


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> "FrozenDatetime":
        instant = cls(2026, 10, 7, 12, tzinfo=timezone.utc)
        return instant.replace(tzinfo=None) if tz is None else instant.astimezone(tz)


@pytest.mark.parametrize("store_type", [S2PTestGraphStore, EvolverGraphStore])
def test_frozen_clock_preserves_reset_outcomes_selection_and_promotion(
    store_type: type[OrderedEvolutionGraphStore],
) -> None:
    with (
        patch("tests.ordered_evolution_store.datetime", FrozenDatetime),
        patch("copilot_sdk.graph.memory_store.datetime", FrozenDatetime),
    ):
        s2p_evolver.set_graph_store(store_type(domain="s2p"))
        s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", is_correct=True)
        s2p_evolver.reset_s2p_evolver()

        # The first registered active variant is the initial selection by convention.
        selected = s2p_evolver.get_active_variant()
        assert selected is not None
        assert selected["id"] == "EVIDENCE_ORDER_v1"

        s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v1", reward=-0.1)
        s2p_evolver.record_triage_outcome(
            "EVIDENCE_ORDER_v1", reward=-1.0, is_correct=True
        )
        stats = next(
            row for row in s2p_evolver.get_evolution_summary()["variants"]
            if row["id"] == "EVIDENCE_ORDER_v1"
        )
        assert (stats["successes"], stats["failures"], stats["total"]) == (1, 1, 2)

        for _ in range(S2P_EVOLVER_CONFIG.promotion_min_samples):
            s2p_evolver.record_triage_outcome("EVIDENCE_ORDER_v2", is_correct=True)
        promotion = s2p_evolver.check_promotion({"status": "GREEN"})
        assert promotion is not None
        assert promotion["promoted_id"] == "EVIDENCE_ORDER_v2"
        assert promotion["previous_id"] == "EVIDENCE_ORDER_v1"


def test_ordered_event_retry_is_idempotent_and_does_not_mutate_metadata() -> None:
    store = OrderedEvolutionGraphStore(domain="s2p")
    metadata = {"success": True}
    for _ in range(2):
        store.write_evolution_event(
            event_id="retry", domain="s2p", event_type="variant_outcome",
            rule_name="evidence_ordering", variant_id="EVIDENCE_ORDER_v1",
            metadata=metadata,
        )
    assert metadata == {"success": True}
    assert len(store.get_evolution_events("s2p")) == 1
