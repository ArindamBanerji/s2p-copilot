"""Restart-safe, source-reconciled S2P compounding ledger on AGE events."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from uuid import uuid4

from app.domains.s2p.proposals import DecisionChangeProposal
from app.services.proposal_service import ProposalStore
from copilot_sdk.graph.protocol import GraphStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class CompoundingLedger:
    """Persist live S2P observations as append-only AGE events."""

    domain = "s2p"

    def __init__(
        self,
        proposal_store: ProposalStore,
        graph_store: GraphStore,
        *,
        iks_provider: Callable[[], Mapping[str, Any] | float | None] | None = None,
        conservation_provider: Callable[[], Mapping[str, Any] | None] | None = None,
    ) -> None:
        if graph_store is None:
            raise ValueError("S2P compounding ledger requires a GraphStore")
        self.proposal_store = proposal_store
        self.graph_store = graph_store
        self.iks_provider = iks_provider
        self.conservation_provider = conservation_provider

    def _events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        reader = getattr(self.graph_store, "get_evolution_events", None)
        if not callable(reader):
            raise RuntimeError("S2P GraphStore does not expose evolution-event reads")
        kwargs = {} if event_type is None else {"event_type": event_type}
        return [dict(row) for row in reader(self.domain, limit=10_000, **kwargs)]

    @staticmethod
    def _metadata(event: Mapping[str, Any]) -> dict[str, Any]:
        raw = event.get("metadata") or event.get("metadata_json") or "{}"
        if isinstance(raw, Mapping):
            return dict(raw)
        try:
            value = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("S2P AGE ledger event contains invalid metadata") from exc
        if not isinstance(value, dict):
            raise RuntimeError("S2P AGE ledger event metadata must be an object")
        return value

    def _write(self, event_type: str, payload: Mapping[str, Any], category: str = "ledger") -> None:
        self.graph_store.write_evolution_event(
            event_id=f"s2p-ledger:{event_type}:{uuid4().hex}",
            domain=self.domain,
            event_type=event_type,
            rule_name=category,
            variant_id="s2p-ledger",
            metadata=dict(_json_safe(dict(payload))),
        )

    def close(self) -> None:
        """The application owns the shared GraphStore lifecycle."""

    def record_iks(self, iks_value: float, *, observed_at: str | None = None, source: str = "scorer", metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        value = _finite(iks_value)
        if value is None:
            raise ValueError("iks_value must be finite")
        point = {"timestamp": observed_at or _now_iso(), "iks_value": value, "source": source, "metadata": _json_safe(dict(metadata or {}))}
        self._write("s2p_iks_observation", point)
        return point

    def record_conservation(self, state: Mapping[str, Any], *, observed_at: str | None = None, source: str = "scorer") -> dict[str, Any]:
        payload = dict(state)
        verified = _finite(payload.get("verified_count"))
        correct = _finite(payload.get("correct_count"))
        q = _finite(payload.get("q"))
        if q is None and verified is not None and verified > 0 and correct is not None:
            q = max(0.0, min(correct / verified, 1.0))
        point = {"timestamp": observed_at or _now_iso(), "phase": None if payload.get("phase") is None else str(payload["phase"]), "alpha": _finite(payload.get("alpha")), "q": q, "status": str(payload.get("status") or payload.get("state") or ""), "source": source, "metadata": _json_safe(payload)}
        self._write("s2p_conservation_observation", point)
        return point

    def refresh_live_observations(self) -> None:
        if self.iks_provider is not None:
            value = self.iks_provider()
            if isinstance(value, Mapping):
                iks = _finite(value.get("iks_value", value.get("iks")))
                if iks is not None:
                    self.record_iks(iks, observed_at=str(value.get("timestamp") or _now_iso()), source=str(value.get("source") or "scorer"), metadata=value)
            elif value is not None:
                iks = _finite(value)
                if iks is not None:
                    self.record_iks(iks)
        if self.conservation_provider is not None:
            state = self.conservation_provider()
            if state is not None:
                self.record_conservation(state)

    def record_governance_event(self, event_type: str, category: str | None, payload: Mapping[str, Any]) -> dict[str, Any]:
        event = {"timestamp": _now_iso(), "event_type": event_type, "category": category, **_json_safe(dict(payload))}
        self._write("s2p_governance_event", event, category or "governance")
        return event

    def timeline(self, limit: int = 100) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        proposals = self.proposal_store.list_recent(max(int(limit), 0))
        for proposal in proposals:
            entries.append(self._proposal_event(proposal))
            outcome = self.proposal_store.get_outcome(proposal.proposal_id)
            if outcome is not None and proposal.outcome_receipt_id:
                entries.append(self._outcome_event(proposal, outcome))
        for event in self._events():
            payload = self._metadata(event)
            payload.setdefault("timestamp", str(event.get("created_at") or event.get("timestamp") or ""))
            payload.setdefault("event_type", str(event.get("event_type") or ""))
            entries.append(payload)
        entries.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return entries[: max(int(limit), 0)]

    def summary(self) -> dict[str, Any]:
        proposals = self.proposal_store.list_recent(max(self.proposal_store.count(), 1))
        verified: list[tuple[DecisionChangeProposal, Mapping[str, Any]]] = []
        for proposal in proposals:
            outcome = self.proposal_store.get_outcome(proposal.proposal_id)
            if outcome is not None and proposal.outcome_receipt_id:
                verified.append((proposal, outcome))
        correct = sum(1 for _, outcome in verified if bool(outcome.get("correct")))
        return {"total_decisions": len(proposals), "verified_outcomes": len(verified), "correct_outcomes": correct, "accuracy": (correct / len(verified)) if verified else None, "total_impact": sum(self._impact_value(outcome.get("measured_impact")) or 0.0 for _, outcome in verified), "per_category": {}, "savings_rate": None, "measured_impact_count": sum(self._impact_value(outcome.get("measured_impact")) is not None for _, outcome in verified), "evidence_tier": "T_O" if verified else "T_S"}

    def iks_trajectory(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = [self._metadata(event) for event in self._events("s2p_iks_observation")]
        return list(reversed(rows[-max(int(limit), 0):]))

    def conservation_history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = [self._metadata(event) for event in self._events("s2p_conservation_observation")]
        return list(reversed(rows[-max(int(limit), 0):]))

    @staticmethod
    def _impact_value(value: Any) -> float | None:
        if isinstance(value, Mapping):
            for key in ("total_impact", "financial_impact", "savings_usd", "dollars_saved", "savings", "impact"):
                candidate = _finite(value.get(key))
                if candidate is not None:
                    return candidate
            return None
        return _finite(value)

    @staticmethod
    def _proposal_event(proposal: DecisionChangeProposal) -> dict[str, Any]:
        return {"timestamp": proposal.created_at, "event_type": "proposal", "source": "proposal_store", "proposal_id": proposal.proposal_id, "outcome_receipt_id": proposal.outcome_receipt_id, "category": proposal.category, "action": proposal.proposed_action, "confidence": proposal.confidence, "correct": None, "impact": None, "evidence_tier": "T_S"}

    @staticmethod
    def _outcome_event(proposal: DecisionChangeProposal, outcome: Mapping[str, Any]) -> dict[str, Any]:
        return {"timestamp": str(outcome.get("timestamp") or proposal.created_at), "event_type": "outcome", "source": "verified_outcome", "proposal_id": proposal.proposal_id, "outcome_receipt_id": proposal.outcome_receipt_id, "category": proposal.category, "action": proposal.proposed_action, "confidence": proposal.confidence, "correct": outcome.get("correct"), "impact": CompoundingLedger._impact_value(outcome.get("measured_impact")), "evidence_tier": "T_O"}
