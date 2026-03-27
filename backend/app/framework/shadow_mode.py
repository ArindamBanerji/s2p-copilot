"""
ShadowModeService — Phase 4 shadow mode (§21).

Shadow mode: system makes decisions but does not act on them.
Analyst actions are recorded separately. Agreement is computed.
CISO Q3 answer: "What's the ROI?" — shadow mode realized numbers.

Reference: docs/soc_copilot_design_v5_6_part1.md §21
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class ShadowModeService:
    """Shadow mode: system makes decisions but does not act on them.
    Analyst actions are recorded separately. Agreement is computed."""

    # Module-level toggle — set via POST /api/soc/shadow/toggle
    SHADOW_ENABLED: bool = False

    @staticmethod
    async def record_shadow_decision(
        decision_id: str,
        system_action: str,
        system_confidence: float,
        category: str,
        neo4j_service: Any,
    ) -> None:
        """Mark a Decision node as shadow_mode=True."""
        await neo4j_service.run_query(
            "MATCH (d:Decision {id: $id}) SET d.shadow_mode = true",
            {"id": decision_id},
        )
        log.debug(
            "[SHADOW] Recorded shadow decision: id=%s action=%s category=%s",
            decision_id, system_action, category,
        )

    @staticmethod
    async def record_analyst_action(
        decision_id: str,
        analyst_action: str,
        neo4j_service: Any,
    ) -> None:
        """Record what the analyst actually did (the ground truth).
        Also sets d.agreement = (d.action = analyst_action) on the node."""
        await neo4j_service.run_query(
            """MATCH (d:Decision {id: $id})
               SET d.analyst_action = $analyst_action,
                   d.agreement = (d.action = $analyst_action)""",
            {"id": decision_id, "analyst_action": analyst_action},
        )
        log.debug("[SHADOW] Analyst action recorded: decision=%s action=%s", decision_id, analyst_action)

    @staticmethod
    async def get_shadow_report(neo4j_service: Any) -> dict:
        """Generate shadow mode report: agreement rates by category.

        Returns
        -------
        {
            "overall_agreement": float,
            "total_shadow_decisions": int,
            "by_category": {category: {"total", "agreed", "agreement_rate"}},
            "recommendation": str,
        }
        """
        try:
            result = await neo4j_service.run_query(
                """
                MATCH (d:Decision)
                WHERE d.shadow_mode = true AND d.analyst_action IS NOT NULL
                RETURN d.category AS category,
                       count(d) AS total,
                       sum(CASE WHEN d.agreement = true THEN 1 ELSE 0 END) AS agreed
                ORDER BY category
                """,
            )
        except Exception as exc:
            log.warning("[SHADOW] get_shadow_report query failed: %s", exc)
            result = []

        categories: dict = {}
        total_agreed = 0
        total_decisions = 0

        for record in result:
            cat    = record.get("category") or "unknown"
            total  = int(record.get("total") or 0)
            agreed = int(record.get("agreed") or 0)
            categories[cat] = {
                "total":          total,
                "agreed":         agreed,
                "agreement_rate": round(agreed / max(total, 1), 4),
            }
            total_agreed    += agreed
            total_decisions += total

        overall = round(total_agreed / max(total_decisions, 1), 4)
        ready   = total_decisions >= 50 and overall >= 0.70

        return {
            "overall_agreement":       overall,
            "total_shadow_decisions":  total_decisions,
            "by_category":             categories,
            "recommendation": (
                "Ready for live mode" if ready else "Continue shadow observation"
            ),
        }


# Module-level singleton
shadow_svc = ShadowModeService()
