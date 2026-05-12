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

Reference: P22 human oversight controls.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

log = logging.getLogger(__name__)


class ConservationStateMachine:
    """Learning gate state for conservation health."""

    VALID_STATES = {"GREEN", "AMBER", "RED"}
    S2P_ACTIONS = {
        "auto_approve",
        "hold_for_review",
        "escalate_to_buyer",
        "flag_leakage",
        "refer_to_specialist",
    }
    PROTECTED_ACTION = "auto_approve"
    PENALTY_RATIO = 5.0

    _LEARNING_DECISIONS = {
        "GREEN": {
            "learning_allowed": True,
            "learning_paused": False,
            "learning_blocked": False,
            "reason": "conservation_green_learning_allowed",
        },
        "AMBER": {
            "learning_allowed": False,
            "learning_paused": True,
            "learning_blocked": False,
            "reason": "conservation_amber_learning_paused",
        },
        "RED": {
            "learning_allowed": False,
            "learning_paused": True,
            "learning_blocked": True,
            "reason": "conservation_red_learning_blocked",
        },
    }

    def __init__(
        self,
        initial_state: str = "GREEN",
        protected_action: str = PROTECTED_ACTION,
        penalty_ratio: float = PENALTY_RATIO,
    ):
        if protected_action not in self.S2P_ACTIONS:
            raise ValueError(f"unknown protected action: {protected_action}")
        self.protected_action = protected_action
        self.penalty_ratio = float(penalty_ratio)
        self.state = self._normalize_state(initial_state)
        self.previous_state = None
        self.transition_reason = "initial_state"
        self.transition_history: List[Dict[str, Any]] = []

    def set_state(
        self,
        state: str,
        reason: str = "",
        initiated_by: str = "system",
    ) -> Dict[str, Any]:
        """Transition to a conservation state and return learning controls."""
        normalized = self._normalize_state(state)
        previous_state = self.state
        self.previous_state = previous_state
        self.state = normalized
        self.transition_reason = reason or self._LEARNING_DECISIONS[normalized]["reason"]

        transition = {
            "previous_state": previous_state,
            "state": normalized,
            "initiated_by": initiated_by,
            "reason": self.transition_reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.transition_history.append(transition)
        return self.learning_decision()

    def learning_decision(self) -> Dict[str, Any]:
        """Return whether learning can mutate centroids in the current state."""
        decision = dict(self._LEARNING_DECISIONS[self.state])
        decision.update({
            "state": self.state,
            "previous_state": self.previous_state,
            "protected_action": self.protected_action,
            "penalty_ratio": self.penalty_ratio,
            "transition_reason": self.transition_reason,
        })
        return decision

    def allows_learning(self) -> bool:
        """True when the current state permits learning mutation."""
        return bool(self.learning_decision()["learning_allowed"])

    def is_learning_paused(self) -> bool:
        """True when learning should pause before mutation."""
        return bool(self.learning_decision()["learning_paused"])

    def is_learning_blocked(self) -> bool:
        """True when learning is blocked by RED conservation state."""
        return bool(self.learning_decision()["learning_blocked"])

    def current_state(self) -> Dict[str, Any]:
        """Return state machine status for control panels."""
        return {
            **self.learning_decision(),
            "transition_history": list(self.transition_history),
        }

    @classmethod
    def _normalize_state(cls, state: str) -> str:
        normalized = str(state or "").upper()
        if normalized not in cls.VALID_STATES:
            raise ValueError(f"unknown conservation state: {state}")
        return normalized


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
        self.conservation_state_machine = ConservationStateMachine()

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
    # Conservation state machine
    # ------------------------------------------------------------------

    async def set_conservation_state(
        self,
        state: str,
        initiated_by: str,
        reason: str,
    ) -> Dict:
        """Set conservation health and apply the corresponding learning control."""
        decision = self.conservation_state_machine.set_state(
            state=state,
            initiated_by=initiated_by,
            reason=reason,
        )

        if decision["learning_paused"]:
            self.scorer.freeze()
        else:
            self.scorer.unfreeze()

        log.info(
            "[INTERVENTION] conservation_state state=%s by=%s reason=%r",
            decision["state"], initiated_by, reason,
        )
        return await self._log_intervention(
            intervention_type="conservation_state",
            initiated_by=initiated_by,
            reason=reason,
            details=decision,
        )

    async def update_conservation_state(
        self,
        state: str,
        initiated_by: str,
        reason: str,
    ) -> Dict:
        """Compatibility alias for callers using update terminology."""
        return await self.set_conservation_state(state, initiated_by, reason)

    def get_learning_control(self) -> Dict:
        """Return current conservation learning decision."""
        return self.conservation_state_machine.learning_decision()

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
            "conservation":             self.conservation_state_machine.current_state(),
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
