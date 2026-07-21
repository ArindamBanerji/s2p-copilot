"""
S2P graph write-back operations.
Writes S2PDecision nodes to Neo4j.
Analogous to SOC write_decision_to_graph().
"""

import asyncio
import inspect
import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypeVar


T = TypeVar("T")


def _uses_async_sessions(driver: Any, session_context: Any) -> bool:
    """Identify the async Neo4j client without relying on a failing `with`."""
    session_factory = getattr(driver, "session")
    wrapped = getattr(session_factory, "__wrapped__", session_factory)
    return (
        inspect.isasyncgenfunction(wrapped)
        or inspect.iscoroutinefunction(wrapped)
        or not hasattr(session_context, "__enter__")
    )


def _run_in_session(
    driver: Any,
    sync_operation: Callable[[Any], T],
    async_operation: Callable[[Any], Any],
) -> T:
    """Run an operation through either supported Neo4j session interface.

    S2P routes are synchronous handlers, so the production AsyncGraphDatabase
    client is bridged in the worker thread with ``asyncio.run``.  A genuine
    synchronous Neo4j driver remains supported for command-line callers.
    """
    session_context = driver.session()
    if not _uses_async_sessions(driver, session_context):
        with session_context as session:
            return sync_operation(session)

    async def run_async() -> T:
        async with session_context as session:
            result = async_operation(session)
            return await result if inspect.isawaitable(result) else result

    return asyncio.run(run_async())


def write_s2p_decision(
    driver,
    event_id: str,
    category: str,
    action: str,
    action_index: int,
    confidence: float,
    factor_vector: list[float],
    factor_names: list[str],
    supplier_id: str,
    amount: float,
) -> str:
    """
    Write a scored S2P decision to Neo4j.
    Returns decision_id.
    """
    ts          = datetime.now(timezone.utc).isoformat()
    decision_id = f"S2P-{event_id}-{ts[:19].replace(':', '-')}"

    query = """
    MERGE (d:S2PDecision {decision_id: $decision_id})
    SET d.event_id      = $event_id,
        d.category      = $category,
        d.action        = $action,
        d.action_index  = $action_index,
        d.confidence    = $confidence,
        d.factor_vector = $factor_vector,
        d.factor_names  = $factor_names,
        d.supplier_id   = $supplier_id,
        d.amount        = $amount,
        d.timestamp     = $timestamp,
        d.outcome       = null
    RETURN d.decision_id AS decision_id
    """

    parameters = {
        "decision_id": decision_id,
        "event_id": event_id,
        "category": category,
        "action": action,
        "action_index": action_index,
        "confidence": confidence,
        "factor_vector": json.dumps(factor_vector),
        "factor_names": json.dumps(factor_names),
        "supplier_id": supplier_id,
        "amount": amount,
        "timestamp": ts,
    }

    def sync_write(session: Any) -> str:
        result = session.run(query, **parameters)
        record = result.single()
        return str(record["decision_id"])

    async def async_write(session: Any) -> str:
        result = await session.run(query, **parameters)
        record = await result.single()
        return str(record["decision_id"])

    return _run_in_session(driver, sync_write, async_write)


def write_s2p_outcome(
    driver,
    decision_id: str,
    outcome: str,
    analyst_action: str,
    analyst_id: str,
) -> bool:
    """
    Write analyst outcome to existing S2PDecision node.
    Returns True if decision found and updated, False if not found.
    """
    query = """
    MATCH (d:S2PDecision {decision_id: $decision_id})
    SET d.outcome         = $outcome,
        d.analyst_action  = $analyst_action,
        d.analyst_id      = $analyst_id,
        d.outcome_ts      = $outcome_ts
    RETURN d.decision_id AS decision_id
    """
    outcome_ts = datetime.now(timezone.utc).isoformat()

    parameters = {
        "decision_id": decision_id,
        "outcome": outcome,
        "analyst_action": analyst_action,
        "analyst_id": analyst_id,
        "outcome_ts": outcome_ts,
    }

    def sync_write(session: Any) -> bool:
        result = session.run(query, **parameters)
        record = result.single()
        return record is not None

    async def async_write(session: Any) -> bool:
        result = await session.run(query, **parameters)
        record = await result.single()
        return record is not None

    return _run_in_session(driver, sync_write, async_write)


def get_s2p_decision(driver, decision_id: str) -> Optional[dict]:
    """Retrieve a decision by ID. Returns None if not found."""
    query = """
    MATCH (d:S2PDecision {decision_id: $decision_id})
    RETURN d
    """
    def sync_get(session: Any) -> Optional[dict]:
        result = session.run(query, decision_id=decision_id)
        record = result.single()
        if not record:
            return None
        return dict(record["d"])

    async def async_get(session: Any) -> Optional[dict]:
        result = await session.run(query, decision_id=decision_id)
        record = await result.single()
        if not record:
            return None
        return dict(record["d"])

    return _run_in_session(driver, sync_get, async_get)
