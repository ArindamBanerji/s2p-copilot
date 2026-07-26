"""
S2P graph write-back operations.
Writes S2PDecision nodes to Neo4j.
Analogous to SOC write_decision_to_graph().
"""

import asyncio
import inspect
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
