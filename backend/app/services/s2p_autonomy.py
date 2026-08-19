"""S2P wiring for shared earned-authority and Frozen Twin mechanisms."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from copilot_sdk.evolution import ScorerBackedProvider
from copilot_sdk.graph.protocol import GraphStore
from copilot_sdk.promotion import PromotionEngine, PromotionResult, PromotionStage, PromotionStore, S2PPromotionPolicy
from copilot_sdk.twin import FrozenTwin, FrozenTwinStore

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import S2PGraphReader


class S2PAutonomyManager:
    """Persist per-category authority and an immutable S2P day-0 baseline."""

    def __init__(self, data_dir: str | Path, scorer: Any, ledger: Any | None = None) -> None:
        self.scorer = scorer
        self.ledger = ledger
        data_path = Path(data_dir)
        self.conservation = ScorerBackedProvider(scorer, "s2p")
        self.engine = PromotionEngine(policy=S2PPromotionPolicy(), store=PromotionStore(str(data_path / "s2p_promotion.sqlite3")), conservation_provider=self.conservation)
        self.twin = FrozenTwin(FrozenTwinStore(data_path / "frozen_twins"))
        try:
            self.twin.load("s2p")
        except FileNotFoundError:
            pass
        for category in S2PDomainConfig.categories:
            if self.engine.store.load_by_class("s2p", category) is None:
                self.engine.create("s2p", category)

    def evidence_tier(self) -> str:
        store = getattr(self.scorer, "graph_store", None)
        if store is None or not isinstance(store, GraphStore):
            return "T_S"
        reader = S2PGraphReader(store=store)
        return "T_O" if reader.count_verified_decisions() > 0 else "T_S"

    def statuses(self) -> list[dict[str, Any]]:
        records = {record.decision_class: record for record in self.engine.get_all("s2p")}
        tier = self.evidence_tier()
        return [{**records[category].to_dict(), "authority": records[category].current_stage.value, "evidence_tier": tier, "evidence_label": "observed/measured" if tier == "T_O" else "synthetic/modelled until verified"} for category in S2PDomainConfig.categories if category in records]

    def record_for(self, category: str):
        if category not in S2PDomainConfig.categories:
            raise ValueError(f"Unknown S2P decision category: {category}")
        record = self.engine.store.load_by_class("s2p", category)
        return record if record is not None else self.engine.create("s2p", category)

    def advance(self, category: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
        record = self.record_for(category)
        payload = dict(evidence)
        payload.setdefault("evidence_tier", self.evidence_tier())
        result = self.engine.advance(record.record_id, payload)
        output = self._result_payload(result, category)
        self._record_event("promotion_advanced", category, output)
        return output

    def rollback(self, category: str, reason: str) -> dict[str, Any]:
        record = self.record_for(category)
        result = self.engine.rollback(record.record_id, reason)
        output = self._result_payload(result, category)
        self._record_event("promotion_rollback", category, output)
        return output

    def transfer(self, category: str, target_category: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
        record = self.record_for(category)
        if target_category not in S2PDomainConfig.categories:
            raise ValueError(f"Unknown S2P target category: {target_category}")
        payload = dict(evidence)
        payload.setdefault("evidence_tier", self.evidence_tier())
        result = self.engine.transfer(record.record_id, "s2p", target_category, payload)
        output = self._result_payload(result, category)
        self._record_event("promotion_transfer", category, {**output, "target_category": target_category})
        return output

    def freeze(self) -> dict[str, Any]:
        trajectory = self.scorer.trajectory()
        snapshot = self.twin.freeze(getattr(self.scorer, "_scorer", self.scorer), dict(self.conservation.get_state()), float(getattr(trajectory, "current_iks", 0.0)), "s2p")
        output = {"frozen": True, "copilot": "s2p", "checksum": snapshot.checksum, "metadata": snapshot.metadata, "evidence_tier": "T_A"}
        self._record_event("frozen_twin_created", None, output)
        return output

    def twin_status(self) -> dict[str, Any]:
        frozen = self.twin.is_frozen()
        return {"frozen": frozen, "copilot": "s2p", "evidence_tier": "T_A" if frozen else "T_S"}

    def drift(self) -> dict[str, Any]:
        report = self.twin.get_drift_report(getattr(self.scorer, "_scorer", self.scorer))
        return {**asdict(report), "evidence_tier": "T_A", "evidence_label": "computed from frozen and live scorer state"}

    def parallel_score(self, factor_vector: list[float], category: str) -> dict[str, Any] | None:
        if not self.twin.is_frozen():
            return None
        result = self.twin.score_parallel(factor_vector, S2PDomainConfig.get_category_index(category), getattr(self.scorer, "_scorer", self.scorer))
        output = {"live": _score_payload(result.live_result), "frozen": _score_payload(result.frozen_result), "confidence_delta": float(result.delta), "evidence_tier": "T_A"}
        self._record_event("frozen_twin_comparison", category, output)
        return output

    def _record_event(self, event_type: str, category: str | None, payload: Mapping[str, Any]) -> None:
        recorder = getattr(self.ledger, "record_governance_event", None)
        if callable(recorder):
            recorder(event_type, category, payload)

    @staticmethod
    def _result_payload(result: PromotionResult, category: str) -> dict[str, Any]:
        return {"category": category, "advanced": result.advanced, "new_stage": result.new_stage.value, "reason": result.reason, "record": None if result.record is None else result.record.to_dict(), "target_record_id": result.target_record_id, "evidence_tier": "T_O" if result.advanced else "T_S"}


def _score_payload(result: Any) -> dict[str, Any]:
    return {"action": str(getattr(result, "action", "")), "confidence": float(getattr(result, "confidence", 0.0)), "action_index": int(getattr(result, "action_index", 0))}
