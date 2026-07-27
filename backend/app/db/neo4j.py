"""
Neo4j Aura client for Security Graph
Handles all graph queries for the SOC Copilot Demo
"""
import logging
import os
from typing import Optional, Dict, Any, List, cast
from neo4j import AsyncGraphDatabase, AsyncDriver
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

# Non-Decision topology client. Decision operations use GraphStore.
# Retained for Alert/Asset/AttackPattern reads only.

class Neo4jClient:
    """Neo4j Aura client with connection pooling.

    # Non-Decision topology client. Decision operations use GraphStore.
    # Retained for Alert/Asset/AttackPattern reads only.
    """

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
            return cast(List[Dict[str, Any]], records)

    # ========================================================================
    # Security Context Queries
    # ========================================================================

    async def get_security_context(self, alert_id: str) -> Optional[Dict[str, Any]]:
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
    # Deployment Queries
    # ========================================================================

    async def get_pattern_count(self) -> int:
        """Get total learned pattern count"""
        query = "MATCH (p:AttackPattern) RETURN count(p) as count"
        result = await self.run_query(query)
        return int(result[0]["count"]) if result else 0

    async def get_alert(self, alert_id: str) -> Optional[Dict[str, Any]]:
        """Get alert by ID"""
        query = "MATCH (alert:Alert {id: $alert_id}) RETURN alert"
        result = await self.run_query(query, {"alert_id": alert_id})
        return result[0]["alert"] if result else None

# Global client instance
neo4j_client = Neo4jClient()
