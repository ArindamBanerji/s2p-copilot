"""Shadow-only S2P auto-approve gate.

P40B is advisory only. It evaluates what would happen, records an in-process
shadow log, and never writes outcomes or calls scorer learning paths.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import random
from typing import Any, Literal
from uuid import uuid4

from app.domains.s2p.config import S2PDomainConfig
from app.services.novelty_tracker import get_novelty_tracker


AUTO_APPROVE_ACTION = "auto_approve"
DOMAIN = "s2p"
TREND_MIN_DECISIONS = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class AutoApproveConfig:
    enabled: bool = False
    mode: Literal["disabled", "shadow"] = "disabled"
    initial_threshold: float = 0.95
    min_threshold: float = 0.80
    spot_check_rate: float = 0.02
    min_verified_decisions: int = 100
    random_seed: int | None = None
    require_conservation_green: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "shadow"}:
            raise ValueError("P40B supports disabled or shadow mode only")
        if self.require_conservation_green is not True:
            raise ValueError("P40B requires conservation GREEN and cannot disable this gate")
        if self.enabled and self.mode != "shadow":
            raise ValueError("enabled P40B gate must use shadow mode")
        if not 0.0 <= self.min_threshold <= self.initial_threshold <= 1.0:
            raise ValueError("thresholds must satisfy 0 <= min <= initial <= 1")
        if not 0.0 <= self.spot_check_rate <= 1.0:
            raise ValueError("spot_check_rate must be between 0 and 1")
        if self.min_verified_decisions < 0:
            raise ValueError("min_verified_decisions must be non-negative")


@dataclass
class CategoryAutoApproveState:
    category: str
    threshold: float
    auto_approved_count: int = 0
    pending_verification_count: int = 0
    spot_checked_count: int = 0
    verified_correct_count: int = 0
    verified_incorrect_count: int = 0
    last_threshold_change: str | None = None
    conservation_status: str = "UNKNOWN"
    verified_count: int = 0
    rolling_q: float = 0.0
    derived_category_readiness: str = "blocked"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShadowAutoApproveEvent:
    event_id: str
    decision_id: str | None
    invoice_id: str | None
    supplier_id: str | None
    category: str
    recommended_action: str
    confidence: float
    threshold: float
    gate_decision: str
    status: Literal["shadow_only", "blocked", "spot_check_required"]
    blocked_reason: str | None
    conservation_status_at_decision: str
    verified_count_at_decision: int
    rolling_q_at_decision: float
    created_at: str
    source: str = "auto_approve_shadow"
    learning_applied: bool = False
    outcome_written: bool = False
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _verified_row_category(row: dict[str, Any]) -> str | None:
    value = row.get("category")
    if value:
        return str(value)
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        category = metadata.get("category")
        return str(category) if category else None
    return None


def _row_is_correct(row: dict[str, Any]) -> bool:
    value = row.get("is_correct")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "correct"}
    return False


class AutoApproveGate:
    def __init__(self, config: AutoApproveConfig | None = None, max_audit_events: int = 200) -> None:
        self.config = config or AutoApproveConfig()
        self._thresholds = {
            category: float(self.config.initial_threshold)
            for category in S2PDomainConfig.categories
        }
        self._states = {
            category: CategoryAutoApproveState(
                category=category,
                threshold=self._thresholds[category],
            )
            for category in S2PDomainConfig.categories
        }
        self._events: list[ShadowAutoApproveEvent] = []
        self._max_audit_events = max(1, int(max_audit_events))
        self._rng = random.Random(self.config.random_seed)

    def configure(self, *, enabled: bool, mode: str = "shadow", **updates: Any) -> dict[str, Any]:
        if mode not in {"disabled", "shadow"}:
            raise ValueError("P40B rejects execution modes; only disabled and shadow are supported")
        config_data = asdict(self.config)
        config_data.update(updates)
        config_data["enabled"] = bool(enabled)
        config_data["mode"] = "shadow" if enabled else "disabled"
        self.config = AutoApproveConfig(**config_data)
        self._rng = random.Random(self.config.random_seed)
        self._thresholds = {
            category: float(self.config.initial_threshold)
            for category in S2PDomainConfig.categories
        }
        for category in S2PDomainConfig.categories:
            state = self._states.setdefault(
                category,
                CategoryAutoApproveState(category=category, threshold=self._thresholds[category]),
            )
            state.threshold = self._thresholds[category]
        return self.config_dict()

    def disable(self) -> dict[str, Any]:
        self.config = AutoApproveConfig(
            enabled=False,
            mode="disabled",
            initial_threshold=self.config.initial_threshold,
            min_threshold=self.config.min_threshold,
            spot_check_rate=self.config.spot_check_rate,
            min_verified_decisions=self.config.min_verified_decisions,
            random_seed=self.config.random_seed,
        )
        return self.config_dict()

    def config_dict(self) -> dict[str, Any]:
        return asdict(self.config)

    def audit_log(self) -> list[dict[str, Any]]:
        return [event.to_dict() for event in self._events]

    def status_by_category(
        self,
        *,
        graph_store: Any | None,
        conservation_status: str,
        p39_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        verified_rows, warnings = self._verified_decisions(graph_store)
        category_states = {
            category: self._category_state(
                category,
                verified_rows=verified_rows,
                conservation_status=conservation_status,
                warnings=list(warnings),
            ).to_dict()
            for category in S2PDomainConfig.categories
        }
        return {
            "config": self.config_dict(),
            "enabled": self.config.enabled,
            "mode": self.config.mode,
            "conservation_status": conservation_status,
            "category_states": category_states,
            "derived_category_readiness": {
                category: state["derived_category_readiness"]
                for category, state in category_states.items()
            },
            "p39_evidence": p39_evidence or {},
            "warnings": warnings,
        }

    def evaluate(
        self,
        *,
        category: str,
        confidence: float,
        recommended_action: str,
        graph_store: Any | None,
        conservation_status: str,
        decision_id: str | None = None,
        invoice_id: str | None = None,
        supplier_id: str | None = None,
        p39_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        verified_rows, warnings = self._verified_decisions(graph_store)
        state = self._category_state(
            category,
            verified_rows=verified_rows,
            conservation_status=conservation_status,
            warnings=list(warnings),
        )
        threshold = state.threshold
        blocked_reason: str | None = None
        status: Literal["shadow_only", "blocked", "spot_check_required"] = "blocked"
        would_auto_approve = False

        if category not in S2PDomainConfig.categories:
            blocked_reason = "unknown_category"
        elif not self.config.enabled or self.config.mode != "shadow":
            blocked_reason = "disabled"
        elif conservation_status != "GREEN":
            blocked_reason = "conservation_not_green"
        elif _novelty_active_for_category(category):
            blocked_reason = "novelty_spike"
        elif state.verified_count < self.config.min_verified_decisions:
            blocked_reason = "insufficient_category_verified_count"
        elif confidence < threshold:
            blocked_reason = "below_threshold"
        elif recommended_action != AUTO_APPROVE_ACTION:
            blocked_reason = "wrong_action"
        elif self.should_spot_check():
            blocked_reason = "spot_check_required"
            status = "spot_check_required"
        else:
            would_auto_approve = True
            status = "shadow_only"

        if blocked_reason and status != "spot_check_required":
            status = "blocked"

        gate_decision = (
            "would_auto_approve"
            if would_auto_approve
            else "spot_check_required"
            if status == "spot_check_required"
            else "blocked"
        )
        event = self.record_shadow_event(
            decision_id=decision_id,
            invoice_id=invoice_id,
            supplier_id=supplier_id,
            category=category,
            recommended_action=recommended_action,
            confidence=confidence,
            threshold=threshold,
            gate_decision=gate_decision,
            status=status,
            blocked_reason=blocked_reason,
            conservation_status=conservation_status,
            verified_count=state.verified_count,
            rolling_q=state.rolling_q,
        )

        return {
            "would_auto_approve": would_auto_approve,
            "blocked_reason": blocked_reason,
            "readiness": state.to_dict(),
            "event": event.to_dict(),
            "shadow_only": True,
            "learning_applied": False,
            "outcome_written": False,
            "verified": False,
            "p39_evidence": p39_evidence or {},
            "warnings": warnings,
        }

    def should_spot_check(self) -> bool:
        return self._rng.random() < self.config.spot_check_rate

    def maybe_expand_threshold_from_verified_outcomes_only(
        self,
        *,
        category: str,
        verified_rows: list[dict[str, Any]],
        conservation_status: str,
    ) -> dict[str, Any]:
        state = self._category_state(
            category,
            verified_rows=verified_rows,
            conservation_status=conservation_status,
            warnings=[],
        )
        if conservation_status != "GREEN":
            return {
                "changed": False,
                "reason": "conservation_not_green",
                "threshold": state.threshold,
            }
        if state.verified_count < self.config.min_verified_decisions:
            return {
                "changed": False,
                "reason": "insufficient_verified_outcomes",
                "threshold": state.threshold,
            }
        if state.rolling_q < 0.95:
            new_threshold = min(1.0, round(state.threshold + 0.05, 4))
            self._thresholds[category] = new_threshold
            self._states[category].threshold = new_threshold
            self._states[category].last_threshold_change = _now_iso()
            return {
                "changed": True,
                "reason": "contracted_after_verified_incorrect_auto_approval",
                "threshold": new_threshold,
            }
        proposed = max(self.config.min_threshold, round(state.threshold - 0.01, 4))
        if proposed < state.threshold:
            self._thresholds[category] = proposed
            self._states[category].threshold = proposed
            self._states[category].last_threshold_change = _now_iso()
            return {
                "changed": True,
                "reason": "expanded_from_verified_outcomes",
                "threshold": proposed,
            }
        return {"changed": False, "reason": "at_min_threshold", "threshold": state.threshold}

    def record_shadow_event(
        self,
        *,
        decision_id: str | None,
        invoice_id: str | None,
        supplier_id: str | None,
        category: str,
        recommended_action: str,
        confidence: float,
        threshold: float,
        gate_decision: str,
        status: Literal["shadow_only", "blocked", "spot_check_required"],
        blocked_reason: str | None,
        conservation_status: str,
        verified_count: int,
        rolling_q: float,
    ) -> ShadowAutoApproveEvent:
        event = ShadowAutoApproveEvent(
            event_id=f"P40B-SHADOW-{uuid4().hex}",
            decision_id=decision_id,
            invoice_id=invoice_id,
            supplier_id=supplier_id,
            category=category,
            recommended_action=recommended_action,
            confidence=float(confidence),
            threshold=float(threshold),
            gate_decision=gate_decision,
            status=status,
            blocked_reason=blocked_reason,
            conservation_status_at_decision=conservation_status,
            verified_count_at_decision=int(verified_count),
            rolling_q_at_decision=float(rolling_q),
            created_at=_now_iso(),
        )
        self._events.append(event)
        if len(self._events) > self._max_audit_events:
            self._events = self._events[-self._max_audit_events :]
        state = self._states.setdefault(
            category,
            CategoryAutoApproveState(category=category, threshold=float(threshold)),
        )
        if status == "shadow_only":
            state.auto_approved_count += 1
        elif status == "spot_check_required":
            state.spot_checked_count += 1
        return event

    def _verified_decisions(self, graph_store: Any | None) -> tuple[list[dict[str, Any]], list[str]]:
        if graph_store is None:
            return [], ["GraphStore unavailable; derived category readiness is blocked."]
        get_verified = getattr(graph_store, "get_verified_decisions", None)
        if not callable(get_verified):
            return [], ["GraphStore verified-decision read API unavailable; derived category readiness is blocked."]
        domain = str(getattr(graph_store, "domain", DOMAIN) or DOMAIN)
        try:
            rows = get_verified(domain)
        except Exception as exc:
            return [], [f"GraphStore verified-decision read failed: {exc}"]
        return [dict(row) for row in rows if isinstance(row, dict)], []

    def _category_state(
        self,
        category: str,
        *,
        verified_rows: list[dict[str, Any]],
        conservation_status: str,
        warnings: list[str],
    ) -> CategoryAutoApproveState:
        category_rows = [row for row in verified_rows if _verified_row_category(row) == category]
        verified_count = len(category_rows)
        correct_count = sum(1 for row in category_rows if _row_is_correct(row))
        rolling_rows = category_rows[-400:]
        rolling_correct = sum(1 for row in rolling_rows if _row_is_correct(row))
        rolling_q = float(rolling_correct) / len(rolling_rows) if rolling_rows else 0.0
        readiness = "ready" if (
            conservation_status == "GREEN"
            and verified_count >= self.config.min_verified_decisions
        ) else "blocked"
        state = self._states.setdefault(
            category,
            CategoryAutoApproveState(
                category=category,
                threshold=self._thresholds.get(category, float(self.config.initial_threshold)),
            ),
        )
        state.conservation_status = conservation_status
        state.verified_count = verified_count
        state.verified_correct_count = correct_count
        state.verified_incorrect_count = max(verified_count - correct_count, 0)
        state.rolling_q = rolling_q
        state.derived_category_readiness = readiness
        state.warnings = list(warnings)
        state.threshold = self._thresholds.get(category, float(self.config.initial_threshold))
        return state


gate = AutoApproveGate()


def _novelty_active_for_category(category: str) -> bool:
    status = get_novelty_tracker().get_status()
    per_category = status.get("per_category", {})
    if not isinstance(per_category, dict):
        return False
    row = per_category.get(category, {})
    if not isinstance(row, dict):
        return False
    return float(row.get("novelty_rate", 0.0) or 0.0) > 0.20
