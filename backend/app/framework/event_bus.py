"""
Lightweight event bus for SOC Copilot (v4.1 — replaced by ci-platform at v4.5).

Event types are frozen dataclasses so they are immutable and hashable.
Handlers registered with subscribe() are called in registration order.

Reference: docs/soc_copilot_design_v1.md §6.3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Type

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event type definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionMade:
    """
    Emitted after a Decision node is written to the graph.
    Channel A: Decision nodes accumulate in the graph.

    Reference: docs/soc_copilot_design_v1.md §6.3 (event emission pattern).
    """
    alert_id: str
    action: str
    confidence: float
    factor_vector: tuple  # immutable — use tuple, not list


@dataclass(frozen=True)
class OutcomeVerified:
    """
    Emitted after a Decision node is marked correct/incorrect.
    Channel B: Outcome markings accumulate in the graph.

    Reference: docs/soc_copilot_design_v1.md §6.3.
    """
    alert_id: str
    decision_id: str
    outcome: str   # "correct" | "incorrect"
    correct: bool  # convenience bool matching d.correct graph property


@dataclass(frozen=True)
class GraphMutated:
    """
    Emitted for every graph write (decision or outcome).
    Provides a single audit channel for all graph state changes.

    Reference: docs/soc_copilot_design_v1.md §6.3.
    """
    mutation_type: str          # "decision" | "outcome"
    affected_entities: tuple    # immutable — use tuple, not list


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------

Handler = Callable[[Any], Coroutine]


class EventBus:
    """
    Lightweight in-process async event bus.

    Replaced by ci-platform production bus at v4.5.
    """

    def __init__(self) -> None:
        self._handlers: Dict[Type, List[Handler]] = {}

    def subscribe(self, event_type: Type, handler: Handler) -> None:
        """
        Register *handler* to be called whenever an event of *event_type* is emitted.

        Parameters
        ----------
        event_type : Type
            One of DecisionMade, OutcomeVerified, GraphMutated.
        handler : async callable
            Called with the event instance as the only argument.
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        log.debug("EventBus.subscribe: %s → %s", event_type.__name__, handler.__name__)

    async def emit(self, event: Any) -> None:
        """
        Call all handlers registered for type(event), in registration order.

        Parameters
        ----------
        event : DecisionMade | OutcomeVerified | GraphMutated
            The event to dispatch.
        """
        event_type = type(event)
        handlers = self._handlers.get(event_type, [])
        log.debug("EventBus.emit: %s → %d handler(s)", event_type.__name__, len(handlers))
        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                log.error(
                    "EventBus handler %s raised for %s: %s",
                    handler.__name__, event_type.__name__, exc,
                )


# Module-level singleton — imported by routers and services
event_bus = EventBus()
