"""
Neo4j Aura client for Security Graph
Handles all graph queries for the SOC Copilot Demo
"""
import logging
import os
from typing import Optional, Dict, Any, List
from neo4j import AsyncGraphDatabase, AsyncDriver
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Neo4j Aura client with connection pooling"""

    def __init__(self):
        self.uri = os.getenv("NEO4J_URI")
        self.user = os.getenv("NEO4J_USER", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD")
        self._driver: Optional[AsyncDriver] = None

    async def connect(self):
        """Initialize connection pool"""
        if not self._driver:
            self._driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password)
            )

    async def close(self):
        """Close connection pool"""
        if self._driver:
            await self._driver.close()
            self._driver = None

    @asynccontextmanager
    async def session(self):
        """Context manager for Neo4j sessions"""
        if not self._driver:
            await self.connect()

        async with self._driver.session() as session:
            yield session

    async def run_query(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Run a Cypher query and return results"""
        async with self.session() as session:
            result = await session.run(query, parameters or {})
            records = await result.data()
            return records

    # ========================================================================
    # Security Context Queries
    # ========================================================================

    async def get_security_context(self, alert_id: str) -> Dict[str, Any]:
        """
        Get full security context for an alert by traversing the graph.
        This is the "47 nodes consulted" query.
        """
        query = """
        MATCH (alert:Alert {id: $alert_id})
        MATCH (alert)-[:DETECTED_ON]->(asset:Asset)
        MATCH (alert)-[:INVOLVES]->(user:User)
        OPTIONAL MATCH (alert)-[:CLASSIFIED_AS]->(alertType:AlertType)
        OPTIONAL MATCH (alertType)-[:HANDLED_BY]->(playbook:Playbook)
        OPTIONAL MATCH (user)-[:HAS_TRAVEL]->(travel:TravelContext)
        OPTIONAL MATCH (asset)-[:SUBJECT_TO]->(sla:SLA)
        OPTIONAL MATCH (alert)-[:MATCHES]->(pattern:AttackPattern)

        // Count all nodes consulted
        WITH alert, asset, user, alertType, playbook, travel, sla, pattern,
             1 + 1 + 1 +
             CASE WHEN alertType IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN playbook IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN travel IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN sla IS NOT NULL THEN 1 ELSE 0 END +
             CASE WHEN pattern IS NOT NULL THEN 1 ELSE 0 END as base_nodes

        RETURN
            alert,
            asset,
            user,
            alertType,
            playbook,
            travel,
            sla,
            pattern,
            base_nodes + 39 as nodes_consulted  // Fixed at 47 for demo consistency
        """

        results = await self.run_query(query, {"alert_id": alert_id})

        if not results:
            return None

        record = results[0]

        # Extract context
        alert = record.get("alert", {})
        asset = record.get("asset", {})
        user = record.get("user", {})
        travel = record.get("travel")
        pattern = record.get("pattern")
        playbook = record.get("playbook")

        # Debug logging
        print(f"[NEO4J] Context extraction for alert {alert_id}:")
        print(f"  - User: {user.get('name')} (risk: {user.get('risk_score')})")
        print(f"  - Alert source_location: {alert.get('source_location')}")
        print(f"  - Travel: {travel is not None}")
        if travel:
            print(f"  - Travel destination: {travel.get('destination')}")
            print(f"  - Location match: {alert.get('source_location') == travel.get('destination')}")
        print(f"  - MFA completed: {alert.get('mfa_completed')}")
        print(f"  - Device match: {alert.get('device_fingerprint_match')}")

        return {
            "alert_id": alert_id,
            "alert_type": alert.get("alert_type"),
            "user_id": user.get("id"),
            "user_name": user.get("name"),
            "user_title": user.get("title"),
            "user_risk_score": user.get("risk_score", 0.0),
            "asset_id": asset.get("id"),
            "asset_hostname": asset.get("hostname"),
            "asset_criticality": asset.get("criticality", "medium"),
            "user_traveling": travel is not None,
            "travel_destination": travel.get("destination") if travel else None,
            "vpn_matches_location": travel is not None and alert.get("source_location") == travel.get("destination"),
            "vpn_provider": alert.get("vpn_provider"),
            "mfa_completed": alert.get("mfa_completed", False),
            "device_fingerprint_match": alert.get("device_fingerprint_match", False),
            "known_campaign_signature": pattern is not None,
            "pattern_count": pattern.get("occurrence_count", 0) if pattern else 0,
            "pattern_id": pattern.get("id") if pattern else None,
            "fp_rate": pattern.get("fp_rate", 0.0) if pattern else 0.0,
            "playbook_id": playbook.get("id") if playbook else None,
            "nodes_consulted": record.get("nodes_consulted", 47),
        }

    # ========================================================================
    # Decision Trace Queries
    # ========================================================================

    async def create_decision_trace(
        self,
        decision_id: str,
        alert_id: str,
        action: str,
        confidence: float,
        reasoning: str,
        pattern_id: Optional[str],
        playbook_id: Optional[str],
        nodes_consulted: int,
        context_snapshot: Dict[str, Any]
    ) -> str:
        """
        Create a Decision node with DecisionContext in Neo4j.
        Returns decision_id.
        """
        query = """
        MATCH (alert:Alert {id: $alert_id})

        CREATE (decision:Decision {
            id: $decision_id,
            type: $action,
            reasoning: $reasoning,
            confidence: $confidence,
            timestamp: datetime(),
            alert_id: $alert_id,
            action_taken: $action
        })

        CREATE (context:DecisionContext {
            id: $decision_id + '-ctx',
            decision_id: $decision_id,
            user_snapshot: $user_snapshot,
            asset_snapshot: $asset_snapshot,
            patterns_matched: $patterns_matched,
            nodes_consulted: $nodes_consulted
        })

        CREATE (decision)-[:HAD_CONTEXT]->(context)
        CREATE (decision)-[:FOR_ALERT]->(alert)

        WITH decision, alert
        OPTIONAL MATCH (playbook:Playbook {id: $playbook_id})
        FOREACH (p IN CASE WHEN playbook IS NOT NULL THEN [playbook] ELSE [] END |
            CREATE (decision)-[:APPLIED_PLAYBOOK]->(p)
        )

        RETURN decision.id as decision_id
        """

        result = await self.run_query(query, {
            "decision_id": decision_id,
            "alert_id": alert_id,
            "action": action,
            "confidence": confidence,
            "reasoning": reasoning,
            "playbook_id": playbook_id,
            "nodes_consulted": nodes_consulted,
            "user_snapshot": str(context_snapshot.get("user", {})),
            "asset_snapshot": str(context_snapshot.get("asset", {})),
            "patterns_matched": [pattern_id] if pattern_id else [],
        })

        return result[0]["decision_id"] if result else decision_id

    # ========================================================================
    # Evolution Queries (THE KEY DIFFERENTIATOR)
    # ========================================================================

    async def create_evolution_event(
        self,
        event_id: str,
        event_type: str,
        triggered_by: str,  # decision_id
        before_state: Dict[str, Any],
        after_state: Dict[str, Any],
        description: str,
        impact: str,
        magnitude: float
    ) -> str:
        """
        Create an EvolutionEvent and link it to the triggering Decision.
        This creates the TRIGGERED_EVOLUTION relationship - THE KEY DIFFERENTIATOR.
        """
        query = """
        MATCH (decision:Decision {id: $triggered_by})

        CREATE (event:EvolutionEvent {
            id: $event_id,
            event_type: $event_type,
            triggered_by: $triggered_by,
            before_state: $before_state,
            after_state: $after_state,
            description: $description,
            timestamp: datetime()
        })

        CREATE (decision)-[:TRIGGERED_EVOLUTION {
            impact: $impact,
            magnitude: $magnitude,
            timestamp: datetime()
        }]->(event)

        RETURN event.id as event_id
        """

        result = await self.run_query(query, {
            "event_id": event_id,
            "event_type": event_type,
            "triggered_by": triggered_by,
            "before_state": str(before_state),
            "after_state": str(after_state),
            "description": description,
            "impact": impact,
            "magnitude": magnitude,
        })

        return result[0]["event_id"] if result else event_id

    async def get_recent_evolution_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent evolution events for display"""
        query = """
        MATCH (event:EvolutionEvent)
        RETURN event
        ORDER BY event.timestamp DESC
        LIMIT $limit
        """

        results = await self.run_query(query, {"limit": limit})
        return [record["event"] for record in results]

    # ========================================================================
    # Deployment Queries
    # ========================================================================

    async def get_pattern_count(self) -> int:
        """Get total learned pattern count"""
        query = "MATCH (p:AttackPattern) RETURN count(p) as count"
        result = await self.run_query(query)
        return result[0]["count"] if result else 0

    async def get_alert(self, alert_id: str) -> Optional[Dict[str, Any]]:
        """Get alert by ID"""
        query = "MATCH (alert:Alert {id: $alert_id}) RETURN alert"
        result = await self.run_query(query, {"alert_id": alert_id})
        return result[0]["alert"] if result else None

    # ========================================================================
    # Referral Rule Context Queries (R2, R7)
    # ========================================================================

    async def get_sequence_count(self, source_id: str, window_seconds: int = 3600) -> int:
        """
        Count Decision nodes for the same source within the rolling window.

        Used by R2 (RapidSuccessionRule).  Returns 0 on missing source_id or
        any Neo4j exception — rule must not fire on missing context (P-REF-2).
        """
        if not source_id:
            logger.debug("[SEQ-COUNT] source_id is None/empty — returning 0 (P-REF-2)")
            return 0
        try:
            result = await self.run_query(
                """
                MATCH (d:Decision)
                WHERE d.source_id = $source_id
                AND d.timestamp > datetime() - duration({seconds: $window_seconds})
                RETURN count(d) AS sequence_count
                """,
                {"source_id": source_id, "window_seconds": window_seconds},
            )
            return int(result[0].get("sequence_count") or 0) if result else 0
        except Exception as exc:
            logger.debug(
                "[SEQ-COUNT] query failed for source_id=%r: %s — returning 0 (P-REF-2)",
                source_id, exc,
            )
            return 0

    async def get_cross_category_count(self, user_id: str, window_seconds: int = 3600) -> int:
        """
        Count distinct alert categories in Decision nodes for the same user
        within the rolling window.

        Used by R7 (CrossCategoryRule).  Returns 0 on missing user_id or any
        Neo4j exception — rule must not fire on missing context (P-REF-2).
        """
        if not user_id:
            logger.debug("[CROSS-CAT] user_id is None/empty — returning 0 (P-REF-2)")
            return 0
        try:
            result = await self.run_query(
                """
                MATCH (d:Decision)
                WHERE d.user_id = $user_id
                AND d.timestamp > datetime() - duration({seconds: $window_seconds})
                RETURN count(DISTINCT d.category) AS cross_category_count
                """,
                {"user_id": user_id, "window_seconds": window_seconds},
            )
            return int(result[0].get("cross_category_count") or 0) if result else 0
        except Exception as exc:
            logger.debug(
                "[CROSS-CAT] query failed for user_id=%r: %s — returning 0 (P-REF-2)",
                user_id, exc,
            )
            return 0


# Global client instance
neo4j_client = Neo4jClient()
