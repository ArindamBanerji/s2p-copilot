"""
SimilarCaseFinder ABC for CopilotFramework.
Domain implementations supply SOC/S2P-specific graph queries.
Safe to copy to copilot-sdk.

Design
------
get_theta() is abstract — each domain provides its own per-category
cosine similarity thresholds (SOC has PROD-3 calibrated values;
a fraud copilot would have different ones).

All other methods are generic and live here:
  cosine_similarity()       — directional factor-profile matching
  _fetch_verified_decisions() — Decision-node retrieval by category
  get_similar_cases()       — top-k retrieval with θ filtering
  get_agreement_pct()       — action agreement fraction

Reference: docs/soc_copilot_design_v5_6_part1.md §23.4
"""

from __future__ import annotations

import abc
import logging
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)

# ── Generic §23.4 defaults ───────────────────────────────────────────────────

SIMILAR_CASES_K         = 3     # top-k results in sidebar
SIMILAR_CASES_MIN_PRIOR = 5     # suppress sidebar if fewer decisions in category
SIMILAR_CASES_MAX_SCAN  = 500   # max verified decisions fetched per query (perf SLA)


# ── Abstract base ────────────────────────────────────────────────────────────

class SimilarCasesBase(abc.ABC):
    """Case-based reasoning retrieval — domain subclass supplies get_theta()."""

    # ── Cosine similarity ────────────────────────────────────────────────────

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        """Return cosine similarity in [0, 1].  Returns 0.0 for zero vectors."""
        a = np.asarray(v1, dtype=np.float64)
        b = np.asarray(v2, dtype=np.float64)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        return float(np.dot(a, b) / denom) if denom > 0.0 else 0.0

    # ── θ lookup — domain-specific ───────────────────────────────────────────

    @abc.abstractmethod
    def get_theta(self, category: str) -> float:
        """Return per-category cosine similarity threshold for retrieval."""

    # ── Neo4j query ──────────────────────────────────────────────────────────

    async def _fetch_verified_decisions(
        self,
        category: str,
        neo4j_client: Any,
        limit: int = SIMILAR_CASES_MAX_SCAN,
    ) -> List[Dict[str, Any]]:
        """
        Fetch up to *limit* verified Decision nodes for *category* from Neo4j,
        most-recent first.

        Returns a list of dicts with keys:
          decision_id, action, confidence, outcome, factor_vector, timestamp
        """
        try:
            rows = await neo4j_client.run_query(
                """
                MATCH (d:Decision)
                WHERE d.category = $category
                  AND d.factor_vector IS NOT NULL
                  AND d.outcome IS NOT NULL
                RETURN d.id            AS decision_id,
                       d.action        AS action,
                       d.confidence    AS confidence,
                       d.outcome       AS outcome,
                       d.factor_vector AS factor_vector,
                       d.timestamp     AS timestamp
                ORDER BY d.timestamp DESC
                LIMIT $limit
                """,
                {"category": category, "limit": limit},
            )
        except Exception as exc:
            log.warning("[SIMILAR-CASES] Neo4j query failed for category=%r: %s", category, exc)
            return []

        results = []
        for row in rows:
            fv = row.get("factor_vector")
            if isinstance(fv, str):
                try:
                    import json
                    fv = json.loads(fv)
                except Exception:
                    continue
            if not isinstance(fv, (list, tuple)) or len(fv) == 0:
                continue
            results.append({
                "decision_id": row.get("decision_id"),
                "action":      row.get("action"),
                "confidence":  float(row.get("confidence") or 0.0),
                "outcome":     row.get("outcome"),
                "factor_vector": [float(x) for x in fv],
                "timestamp":   row.get("timestamp"),
            })
        return results

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_similar_cases(
        self,
        factor_vector: List[float],
        category: str,
        neo4j_client: Any,
        k: int = SIMILAR_CASES_K,
    ) -> List[Dict[str, Any]]:
        """
        Return up to k similar past Decision nodes for *category*.

        Category filter is non-negotiable — never returns cross-category results.
        Returns [] if fewer than SIMILAR_CASES_MIN_PRIOR verified decisions exist.

        Each returned dict adds a 'similarity' key (float in [0,1]).
        """
        decisions = await self._fetch_verified_decisions(category, neo4j_client)

        if len(decisions) < SIMILAR_CASES_MIN_PRIOR:
            log.debug(
                "[SIMILAR-CASES] Suppressing sidebar: only %d verified decisions "
                "in category=%r (min=%d)",
                len(decisions), category, SIMILAR_CASES_MIN_PRIOR,
            )
            return []

        theta = self.get_theta(category)
        scored: List[tuple] = []

        for d in decisions:
            sim = self.cosine_similarity(factor_vector, d["factor_vector"])
            if sim >= theta:
                scored.append((sim, d))

        scored.sort(key=lambda x: (-x[0], str(x[1].get("timestamp") or "")))

        results = []
        for sim, d in scored[:k]:
            entry = dict(d)
            entry["similarity"] = round(sim, 4)
            results.append(entry)

        return results

    def get_agreement_pct(
        self,
        similar_cases: List[Dict[str, Any]],
        current_action: str,
    ) -> Optional[float]:
        """
        Return fraction of *similar_cases* whose action matches *current_action*.

        Returns None when similar_cases is empty (suppressed sidebar — cold start).
        Caller should then use the fallback template wording:
          "Calibrated from {calibration_count} verified outcomes." (no pct cited).
        """
        if not similar_cases:
            return None
        matching = sum(1 for c in similar_cases if c.get("action") == current_action)
        return matching / len(similar_cases)
