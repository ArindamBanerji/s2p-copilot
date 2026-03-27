"""
S2P graph write-back operations.
Writes S2PDecision nodes to Neo4j.
Analogous to SOC write_decision_to_graph().
"""

import json
from datetime import datetime, timezone
from typing import Optional


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

    with driver.session() as session:
        result = session.run(query,
            decision_id   = decision_id,
            event_id      = event_id,
            category      = category,
            action        = action,
            action_index  = action_index,
            confidence    = confidence,
            factor_vector = json.dumps(factor_vector),
            factor_names  = json.dumps(factor_names),
            supplier_id   = supplier_id,
            amount        = amount,
            timestamp     = ts,
        )
        record = result.single()
        return record["decision_id"]


def get_s2p_decision(driver, decision_id: str) -> Optional[dict]:
    """Retrieve a decision by ID. Returns None if not found."""
    query = """
    MATCH (d:S2PDecision {decision_id: $decision_id})
    RETURN d
    """
    with driver.session() as session:
        result = session.run(query, decision_id=decision_id)
        record = result.single()
        if not record:
            return None
        return dict(record["d"])
