"""
InterventionControls — P22 Consolidated Oversight Panel (L-12).

EU AI Act Article 14: effective human oversight.  Six controls, full audit
trail.  Every action writes an Intervention node to Neo4j with who/when/why.

Controls
--------
1. freeze_all_learning      — freeze all centroid updates globally
2. unfreeze_all_learning    — resume centroid updates
3. freeze_category          — freeze a specific category
4. rollback                 — restore centroid snapshot (supports preview)
5. disable_auto_approve     — force all decisions to human review
6. category_force_review    — force specific category to human review
7. adjust_threshold         — change auto-approve threshold (min 0.50)

Reference: docs/soc_copilot_design_v1.md §P22
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)


class InterventionControls:
    """Six-control human oversight panel with Neo4j audit trail."""

    def __init__(
        self,
        db_client: Any,
        scorer: Any,
        checkpoint_service: Any,
        composite_gate: Any,
    ):
        self.db = db_client
        self.scorer = scorer
        self.checkpoint_service = checkpoint_service
        self.gate = composite_gate

    # ------------------------------------------------------------------
    # 1. freeze_all_learning
    # ------------------------------------------------------------------

    async def freeze_all_learning(self, initiated_by: str, reason: str) -> Dict:
        """Freeze all centroid updates globally."""
        self.scorer.freeze()
        log.info(
            "[INTERVENTION] freeze_all_learning by=%s reason=%r", initiated_by, reason
        )
        return await self._log_intervention(
            intervention_type="freeze_all_learning",
            initiated_by=initiated_by,
            reason=reason,
            details={"frozen": True},
        )

    # ------------------------------------------------------------------
    # 2. unfreeze_all_learning
    # ------------------------------------------------------------------

    async def unfreeze_all_learning(self, initiated_by: str, reason: str) -> Dict:
        """Resume centroid updates."""
        self.scorer.unfreeze()
        log.info(
            "[INTERVENTION] unfreeze_all_learning by=%s reason=%r", initiated_by, reason
        )
        return await self._log_intervention(
            intervention_type="freeze_all_learning",
            initiated_by=initiated_by,
            reason=reason,
            details={"frozen": False},
        )

    # ------------------------------------------------------------------
    # 3. freeze_category
    # ------------------------------------------------------------------

    async def freeze_category(
        self, category: str, freeze: bool, initiated_by: str, reason: str
    ) -> Dict:
        """Freeze or unfreeze a specific alert category."""
        if freeze:
            self.gate.FROZEN_CATEGORIES.add(category)
        else:
            self.gate.FROZEN_CATEGORIES.discard(category)
        log.info(
            "[INTERVENTION] freeze_category category=%s freeze=%s by=%s",
            category, freeze, initiated_by,
        )
        return await self._log_intervention(
            intervention_type="freeze_category",
            initiated_by=initiated_by,
            reason=reason,
            details={"category": category, "frozen": freeze},
        )

    # ------------------------------------------------------------------
    # 4. rollback
    # ------------------------------------------------------------------

    async def rollback(
        self,
        snapshot_id: str,
        initiated_by: str,
        reason: str,
        preview: bool = False,
    ) -> Dict:
        """Restore centroid snapshot.

        Parameters
        ----------
        preview : bool — when True, return what would change without applying.
        """
        if preview:
            try:
                result = await self.db.run_query(
                    "MATCH (cp:Checkpoint {id: $id}) RETURN cp",
                    {"id": snapshot_id},
                )
            except Exception as exc:
                return {"error": f"Neo4j query failed: {exc}", "preview": True}

            if not result:
                return {"error": "Checkpoint not found", "preview": True}

            cp = result[0].get("cp") or result[0]
            return {
                "preview": True,
                "snapshot_id": snapshot_id,
                "would_restore_decision_count": int(cp.get("decision_count") or 0),
                "reason": cp.get("reason"),
                "checkpoint_timestamp": str(cp.get("timestamp") or ""),
            }

        # Apply rollback
        from app.framework.checkpoint import CheckpointService

        rollback_result = await CheckpointService.rollback(
            checkpoint_id=snapshot_id,
            scorer=self.scorer,
            neo4j_service=self.db,
        )
        if "error" in rollback_result:
            return rollback_result

        log.info(
            "[INTERVENTION] rollback snapshot_id=%s by=%s", snapshot_id, initiated_by
        )
        record = await self._log_intervention(
            intervention_type="rollback",
            initiated_by=initiated_by,
            reason=reason,
            details={"snapshot_id": snapshot_id, **rollback_result},
        )
        record["preview"] = False
        return record

    # ------------------------------------------------------------------
    # 5. disable_auto_approve
    # ------------------------------------------------------------------

    async def disable_auto_approve(
        self, disabled: bool, initiated_by: str, reason: str
    ) -> Dict:
        """Force all decisions to human review (disabled=True) or restore."""
        self.gate.AUTO_APPROVE_DISABLED = disabled
        log.info(
            "[INTERVENTION] disable_auto_approve disabled=%s by=%s", disabled, initiated_by
        )
        return await self._log_intervention(
            intervention_type="disable_auto_approve",
            initiated_by=initiated_by,
            reason=reason,
            details={"auto_approve_enabled": not disabled},
        )

    # ------------------------------------------------------------------
    # 6. category_force_review
    # ------------------------------------------------------------------

    async def category_force_review(
        self, category: str, force: bool, initiated_by: str, reason: str
    ) -> Dict:
        """Force specific category to human review."""
        if force:
            self.gate.FORCE_REVIEW_CATEGORIES.add(category)
        else:
            self.gate.FORCE_REVIEW_CATEGORIES.discard(category)
        log.info(
            "[INTERVENTION] category_force_review category=%s force=%s by=%s",
            category, force, initiated_by,
        )
        return await self._log_intervention(
            intervention_type="category_force_review",
            initiated_by=initiated_by,
            reason=reason,
            details={"category": category, "force_review": force},
        )

    # ------------------------------------------------------------------
    # 7. adjust_threshold
    # ------------------------------------------------------------------

    async def adjust_threshold(
        self, category: str, new_threshold: float, initiated_by: str, reason: str
    ) -> Dict:
        """Change auto-approve confidence threshold per category.

        Rejects thresholds below 0.50 — below that, auto-approve is not
        meaningfully filtered.
        """
        if new_threshold < 0.50:
            return {
                "error": (
                    "threshold must be >= 0.50 "
                    "(below that, auto-approve is not meaningfully filtered)"
                )
            }
        old_threshold = self.gate.CATEGORY_CONFIDENCE_THRESHOLDS.get(
            category, self.gate.CONFIDENCE_THRESHOLD
        )
        self.gate.CATEGORY_CONFIDENCE_THRESHOLDS[category] = new_threshold
        log.info(
            "[INTERVENTION] adjust_threshold category=%s %.2f→%.2f by=%s",
            category, old_threshold, new_threshold, initiated_by,
        )
        return await self._log_intervention(
            intervention_type="threshold_adjustment",
            initiated_by=initiated_by,
            reason=reason,
            details={
                "category":      category,
                "old_threshold": old_threshold,
                "new_threshold": new_threshold,
            },
        )

    # ------------------------------------------------------------------
    # State + History
    # ------------------------------------------------------------------

    async def get_current_state(self) -> Dict:
        """Return current state of all controls."""
        last_intervention = None
        try:
            rows = await self.db.run_query(
                """MATCH (i:Intervention)
                   RETURN i ORDER BY i.timestamp DESC LIMIT 1""",
            )
            if rows:
                node = rows[0].get("i") or rows[0]
                last_intervention = {
                    "type":         node.get("type"),
                    "initiated_by": node.get("initiated_by"),
                    "timestamp":    str(node.get("timestamp") or ""),
                    "reason":       node.get("reason"),
                }
        except Exception as exc:
            log.warning(
                "[INTERVENTION] get_current_state last_intervention query failed: %s", exc
            )

        return {
            "global_freeze":           bool(getattr(self.scorer, "frozen", False)),
            "auto_approve_enabled":    not bool(
                getattr(self.gate, "AUTO_APPROVE_DISABLED", False)
            ),
            "thresholds":              dict(self.gate.CATEGORY_CONFIDENCE_THRESHOLDS),
            "frozen_categories":       list(getattr(self.gate, "FROZEN_CATEGORIES", set())),
            "force_review_categories": list(
                getattr(self.gate, "FORCE_REVIEW_CATEGORIES", set())
            ),
            "last_intervention":       last_intervention,
        }

    async def get_intervention_history(self, limit: int = 50) -> List[Dict]:
        """Return intervention audit log from Neo4j."""
        try:
            rows = await self.db.run_query(
                """MATCH (i:Intervention)
                   RETURN i.id           AS id,
                          i.type         AS type,
                          i.initiated_by AS initiated_by,
                          i.reason       AS reason,
                          toString(i.timestamp) AS timestamp,
                          i.details      AS details
                   ORDER BY i.timestamp DESC
                   LIMIT $limit""",
                {"limit": limit},
            )
        except Exception as exc:
            log.warning("[INTERVENTION] get_intervention_history failed: %s", exc)
            return []

        records = []
        for r in rows:
            details_raw = r.get("details") or "{}"
            try:
                details = (
                    json.loads(details_raw) if isinstance(details_raw, str) else details_raw
                )
            except Exception:
                details = {}
            records.append({
                "id":           r.get("id"),
                "type":         r.get("type"),
                "initiated_by": r.get("initiated_by"),
                "reason":       r.get("reason"),
                "timestamp":    str(r.get("timestamp") or ""),
                "details":      details,
            })
        return records

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _log_intervention(
        self,
        intervention_type: str,
        initiated_by: str,
        reason: str,
        details: Dict,
    ) -> Dict:
        """Write an Intervention node to Neo4j and return the record dict."""
        intervention_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            await self.db.run_query(
                """CREATE (i:Intervention {
                    id:           $id,
                    type:         $type,
                    initiated_by: $initiated_by,
                    reason:       $reason,
                    timestamp:    datetime(),
                    details:      $details
                })""",
                {
                    "id":           intervention_id,
                    "type":         intervention_type,
                    "initiated_by": initiated_by,
                    "reason":       reason,
                    "details":      json.dumps(details),
                },
            )
        except Exception as exc:
            log.error("[INTERVENTION] _log_intervention write failed: %s", exc)

        return {
            "id":           intervention_id,
            "type":         intervention_type,
            "initiated_by": initiated_by,
            "reason":       reason,
            "timestamp":    timestamp,
            "details":      details,
        }
