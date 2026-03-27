"""
CompositeDiscriminant — multi-signal auto-approve gate (Phase 5).

Uses 13 features derived from ProfileScorer output and graph context to decide
whether a decision qualifies for auto-approval.

Validated by DISC-1: 70.4% coverage at 85% precision (synthetic calibration).

Safety invariants (non-negotiable):
  - suppress action requires confidence >= 0.95 (asymmetric safety gate)
  - maturity gate: category must have >= 50 prior decisions
  - confidence threshold: >= 0.70
  - margin threshold: top-1 vs top-2 probability gap >= 0.30

Reference: docs/project_status_and_plan_v3_part2.md Phase 5 / DISC-1
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.framework.decision_history import DecisionHistoryService

log = logging.getLogger(__name__)


class CompositeDiscriminant:
    """Multi-signal auto-approve gate.

    Uses scorer output features + graph context features.
    Validated by DISC-1: 70.4% coverage at 85% precision (synthetic).
    """

    # Global fallback confidence threshold (used when category not in CATEGORY_CONFIDENCE_THRESHOLDS)
    CONFIDENCE_THRESHOLD      = 0.70
    MARGIN_THRESHOLD          = 0.30
    MIN_CAT_COUNT             = 50
    SUPPRESS_SAFETY_THRESHOLD = 0.95

    # Phase 0b per-category confidence thresholds (A=4 calibration).
    # Derived from cross-experiment validation; tighter categories get higher thresholds.
    # Falls back to CONFIDENCE_THRESHOLD (0.70) for any unmapped category.
    CATEGORY_CONFIDENCE_THRESHOLDS: dict = {
        "credential_access":    0.62,
        "data_exfiltration":    0.67,
        "lateral_movement":     0.62,
        "threat_intel_match":   0.69,
        "cloud_infrastructure": 0.65,
        "insider_threat":       0.70,
    }

    # P22 Intervention Controls — EU AI Act Article 14.
    # Mutated at runtime by InterventionControls; process-level state.
    AUTO_APPROVE_DISABLED: bool = False
    FROZEN_CATEGORIES: set = set()
    FORCE_REVIEW_CATEGORIES: set = set()

    @staticmethod
    async def evaluate(
        score_result: Any,
        category: str,
        factor_vector: Any,
        decision_position: float,
        neo4j_service: Any,
        actions: list | None = None,
        suppress_action_name: str = "suppress",
    ) -> dict:
        """Evaluate whether a decision should be auto-approved.

        Parameters
        ----------
        score_result      : ProfileScorer.score() result
                            (.probabilities, .distances, .confidence, .action_index)
        category          : alert category string (e.g. "credential_access")
        factor_vector     : numpy array shape (6,) or list
        decision_position : position in session (0-1); 0.0 for live triage
        neo4j_service     : object with async run_query()
        actions           : ordered list of action name strings; used to resolve
                            action_index to a name for the suppress safety gate.
                            Pass the domain's action list (e.g. SCORER_ACTIONS).
                            If None or empty, suppress gate is skipped safely.
        suppress_action_name : name of the suppress action (default 'suppress').
                            Override for domains that use a different label.

        Returns
        -------
        {
            "auto_approve":   bool,
            "approval_score": float (0-1),
            "reason_codes":   list[str],
            "features":       dict of all 13 computed feature values,
        }
        """
        # ── Scorer-derived features ──────────────────────────────────────────
        probs = np.array(score_result.probabilities, dtype=np.float64)
        sorted_probs = np.sort(probs)[::-1]
        distances = np.array(score_result.distances, dtype=np.float64)
        sorted_dists = np.sort(distances)
        f = np.asarray(factor_vector, dtype=np.float64).flatten()

        confidence    = float(sorted_probs[0])
        margin        = float(sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) > 1 else confidence
        entropy       = float(-np.sum(probs * np.log(probs + 1e-10)))
        top3_mass     = float(np.sum(sorted_probs[:3]))
        prob_std      = float(np.std(probs))
        dist_ratio    = float(sorted_dists[0] / (sorted_dists[1] + 1e-10)) if len(sorted_dists) > 1 else 0.0
        dist_gap      = float(sorted_dists[1] - sorted_dists[0]) if len(sorted_dists) > 1 else 0.0
        factor_extremity   = float(np.max(f) - np.min(f)) if len(f) > 0 else 0.0
        factor_norm        = float(np.linalg.norm(f))
        factor_center_dist = float(np.linalg.norm(f - 0.5))

        # ── Graph context features ───────────────────────────────────────────
        try:
            cat_stats = await DecisionHistoryService.get_category_stats(
                category, neo4j_service
            )
        except Exception as exc:
            log.warning("[COMPOSITE] category_stats failed: %s", exc)
            cat_stats = {"cat_count": 0, "rolling_accuracy": 0.5, "verified_count": 0}

        cat_count        = int(cat_stats.get("cat_count", 0))
        rolling_accuracy = float(cat_stats.get("rolling_accuracy", 0.5))

        features = {
            "confidence":         round(confidence, 4),
            "margin":             round(margin, 4),
            "entropy":            round(entropy, 4),
            "top3_mass":          round(top3_mass, 4),
            "prob_std":           round(prob_std, 4),
            "dist_ratio":         round(dist_ratio, 4),
            "dist_gap":           round(dist_gap, 4),
            "factor_extremity":   round(factor_extremity, 4),
            "factor_norm":        round(factor_norm, 4),
            "factor_center_dist": round(factor_center_dist, 4),
            "cat_count":          cat_count,
            "rolling_accuracy":   round(rolling_accuracy, 4),
            "decision_position":  round(float(decision_position), 4),
        }

        # ── Rule-based conjunction gate ──────────────────────────────────────
        reason_codes: list[str] = []
        auto_approve = True

        _conf_threshold = CompositeDiscriminant.CATEGORY_CONFIDENCE_THRESHOLDS.get(
            category, CompositeDiscriminant.CONFIDENCE_THRESHOLD
        )
        if confidence < _conf_threshold:
            auto_approve = False
            reason_codes.append(
                f"confidence {confidence:.2f} < {_conf_threshold} ({category})"
            )

        if margin < CompositeDiscriminant.MARGIN_THRESHOLD:
            auto_approve = False
            reason_codes.append(
                f"margin {margin:.2f} < {CompositeDiscriminant.MARGIN_THRESHOLD}"
            )

        if cat_count < CompositeDiscriminant.MIN_CAT_COUNT:
            auto_approve = False
            reason_codes.append(
                f"cat_count {cat_count} < {CompositeDiscriminant.MIN_CAT_COUNT} (maturity gate)"
            )

        # Asymmetric safety: suppress requires a higher confidence bar
        action_idx = getattr(score_result, "action_index", -1)
        _actions = actions or []
        if _actions:
            action_name = _actions[action_idx] if 0 <= action_idx < len(_actions) else None
        else:
            # Fallback: ProfileScorer result may carry action_name directly
            action_name = getattr(score_result, "action_name", None)
        if action_name == suppress_action_name:
            if confidence < CompositeDiscriminant.SUPPRESS_SAFETY_THRESHOLD:
                auto_approve = False
                reason_codes.append(
                    f"suppress requires confidence >= {CompositeDiscriminant.SUPPRESS_SAFETY_THRESHOLD}"
                )

        if not reason_codes:
            reason_codes.append("all gates passed")

        # Approval score: confidence scaled by maturity
        maturity_factor = min(cat_count / CompositeDiscriminant.MIN_CAT_COUNT, 1.0)
        approval_score  = confidence * (0.5 + 0.5 * maturity_factor)

        return {
            "auto_approve":   auto_approve,
            "approval_score": round(approval_score, 4),
            "reason_codes":   reason_codes,
            "features":       features,
        }
