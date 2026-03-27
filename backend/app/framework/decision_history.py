"""
DecisionHistoryService — per-category decision counts and rolling accuracy.

Provides cat_count and rolling_accuracy for CompositeDiscriminant maturity gate.
Queries the last 100 decisions per category (recency-weighted).

Reference: docs/project_status_and_plan_v3_part2.md Phase 5 / DISC-1
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class DecisionHistoryService:
    """Tracks per-category decision counts and rolling accuracy."""

    @staticmethod
    async def get_category_stats(category: str, neo4j_service: Any) -> dict:
        """
        Get decision count and rolling accuracy for a category.

        Uses the last 100 decisions (most-recent-first) to compute rolling
        accuracy, so stale incorrect decisions age out naturally.

        Returns
        -------
        {
            "cat_count":        int   — total decisions in category (up to 100)
            "rolling_accuracy": float — correct / verified, defaults 0.5 if none
            "verified_count":   int   — decisions with a known outcome
        }
        """
        try:
            result = await neo4j_service.run_query(
                """
                MATCH (d:Decision)
                WHERE d.category = $cat
                WITH d ORDER BY d.timestamp DESC LIMIT 100
                RETURN count(d) AS cat_count,
                       sum(CASE WHEN d.outcome = 'correct' THEN 1 ELSE 0 END) AS correct_count,
                       sum(CASE WHEN d.outcome IS NOT NULL THEN 1 ELSE 0 END) AS verified_count
                """,
                {"cat": category},
            )
        except Exception as exc:
            log.warning("[DECISION-HISTORY] query failed for category=%r: %s", category, exc)
            return {"cat_count": 0, "rolling_accuracy": 0.5, "verified_count": 0}

        if not result or int(result[0].get("cat_count") or 0) == 0:
            return {"cat_count": 0, "rolling_accuracy": 0.5, "verified_count": 0}

        r = result[0]
        cat_count    = int(r.get("cat_count") or 0)
        verified     = int(r.get("verified_count") or 0)
        correct      = int(r.get("correct_count") or 0)
        rolling_acc  = (correct / verified) if verified > 0 else 0.5

        return {
            "cat_count":        cat_count,
            "rolling_accuracy": round(rolling_acc, 4),
            "verified_count":   verified,
        }
