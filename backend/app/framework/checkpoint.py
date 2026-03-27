"""
CheckpointService — centroid checkpoint and rollback (TD-033, Phase 4 §17.5).

Creates immutable snapshots of the ProfileScorer centroid tensor (mu) in Neo4j.
Rollback restores a snapshot and freezes the scorer to prevent further drift.
CISO Q4 answer: "What if it's wrong?" — instant revert to any prior checkpoint.

Reference: docs/soc_copilot_design_v5_6_part2.md §17.5
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


class CheckpointService:
    """Centroid checkpoint and rollback (TD-033)."""

    @staticmethod
    async def create_checkpoint(
        scorer: Any,
        neo4j_service: Any,
        reason: str = "manual",
    ) -> str:
        """Snapshot current centroids to a Checkpoint node in Neo4j.

        Parameters
        ----------
        scorer : ProfileScorer — source of mu, counts, decision_count
        neo4j_service : object with async run_query
        reason : str — label stored on the node (e.g. "pre-learning-activation")

        Returns
        -------
        str — the new checkpoint_id (UUID)
        """
        checkpoint_id  = str(uuid.uuid4())
        mu_snapshot    = scorer.mu.tolist()
        counts_snapshot = scorer.counts.tolist() if hasattr(scorer, "counts") else []
        decision_count  = int(getattr(scorer, "decision_count", 0))

        await neo4j_service.run_query(
            """CREATE (cp:Checkpoint {
                id:               $id,
                timestamp:        datetime(),
                reason:           $reason,
                mu_snapshot:      $mu,
                counts_snapshot:  $counts,
                decision_count:   $dc
            })""",
            {
                "id":     checkpoint_id,
                "reason": reason,
                "mu":     json.dumps(mu_snapshot),
                "counts": json.dumps(counts_snapshot),
                "dc":     decision_count,
            },
        )
        log.info(
            "[CHECKPOINT] Created: id=%s reason=%r decision_count=%d",
            checkpoint_id, reason, decision_count,
        )
        return checkpoint_id

    @staticmethod
    async def list_checkpoints(neo4j_service: Any) -> list:
        """Return all Checkpoint nodes ordered by timestamp DESC."""
        try:
            result = await neo4j_service.run_query(
                """MATCH (cp:Checkpoint)
                   RETURN cp.id             AS id,
                          toString(cp.timestamp) AS timestamp,
                          cp.reason         AS reason,
                          cp.decision_count AS decision_count
                   ORDER BY cp.timestamp DESC""",
            )
        except Exception as exc:
            log.warning("[CHECKPOINT] list_checkpoints query failed: %s", exc)
            return []

        return [
            {
                "id":             r.get("id"),
                "timestamp":      str(r.get("timestamp") or ""),
                "reason":         r.get("reason"),
                "decision_count": int(r.get("decision_count") or 0),
            }
            for r in result
        ]

    @staticmethod
    async def rollback(
        checkpoint_id: str,
        scorer: Any,
        neo4j_service: Any,
    ) -> dict:
        """Restore centroids from a Checkpoint node and freeze the scorer.

        Parameters
        ----------
        checkpoint_id : str — UUID of the target Checkpoint node
        scorer : ProfileScorer — will have mu (and counts) mutated in-place
        neo4j_service : object with async run_query

        Returns
        -------
        dict with keys: status, checkpoint_id, frozen, restored_decision_count
        """
        try:
            result = await neo4j_service.run_query(
                "MATCH (cp:Checkpoint {id: $id}) RETURN cp",
                {"id": checkpoint_id},
            )
        except Exception as exc:
            log.warning("[CHECKPOINT] rollback query failed: %s", exc)
            return {"error": f"Neo4j query failed: {exc}"}

        if not result:
            return {"error": "Checkpoint not found"}

        cp = result[0].get("cp") or result[0]

        # Restore mu
        mu_str = cp.get("mu_snapshot") or "[]"
        try:
            mu_restored       = np.array(json.loads(mu_str), dtype=np.float64)
            scorer.mu[:]      = mu_restored
        except Exception as exc:
            log.error("[CHECKPOINT] mu restore failed: %s", exc)
            return {"error": f"mu restore failed: {exc}"}

        # Restore counts (optional — present on newer checkpoints)
        counts_str = cp.get("counts_snapshot") or ""
        if counts_str and hasattr(scorer, "counts"):
            try:
                counts_restored  = np.array(json.loads(counts_str), dtype=np.float64)
                scorer.counts[:] = counts_restored
            except Exception as exc:
                log.debug("[CHECKPOINT] counts restore skipped: %s", exc)

        scorer.freeze()
        restored_dc = int(cp.get("decision_count") or 0)

        log.info(
            "[CHECKPOINT] Rolled back to id=%s (decision_count=%d) — scorer frozen",
            checkpoint_id, restored_dc,
        )
        return {
            "status":                   "rolled_back",
            "checkpoint_id":            checkpoint_id,
            "frozen":                   True,
            "restored_decision_count":  restored_dc,
        }


# Module-level singleton
checkpoint_svc = CheckpointService()
