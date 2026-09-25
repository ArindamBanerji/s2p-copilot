"""Explicit, provenance-labelled demo operations; never selected by pytest."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from copilot_sdk.scoring.presets.s2p import S2PPreset
from copilot_sdk.twin import FrozenTwin
from app.domains.s2p.config import S2PDomainConfig
from app.services.s2p_autonomy import S2PAutonomyManager
from copilot_sdk.scoring.mutation_lock import get_mutation_lock

router = APIRouter(prefix="/api/demo/s2p", tags=["s2p-demo"])
DAY_ZERO_EVENT = "PW-DAY-ZERO-001"
FIXTURES = Path(__file__).resolve().parents[3] / "data/demo_fixtures/s2p_demo_decisions.json"


def demo_enabled() -> bool:
    return os.environ.get("S2P_DEMO_MODE", "").lower() in {"1", "true", "yes"}


def demo_presentation_enabled() -> bool:
    """Read/presentation opt-in survives removal of the preseed mutation flag."""
    return demo_enabled() or os.environ.get("S2P_DEMO_READ_MODE", "").lower() in {"1", "true", "yes"}


def require_demo() -> None:
    if not demo_enabled():
        raise HTTPException(status_code=403, detail="S2P_DEMO_MODE must be enabled")


def special_invoice(event_id: str, category: str, supplier_id: str) -> dict[str, Any] | None:
    """Opt-in fixture overlay, constrained by identity; never changes normal data."""
    if not demo_presentation_enabled():
        return None
    try:
        data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="S2P demo invoice fixtures unavailable") from exc
    for row in data["special_decisions"]:
        if (row["event_id"], row["category"], row["supplier_id"]) == (event_id, category, supplier_id):
            return {
                **row, "invoice_id": event_id,
                "factors": {name: row[name] for name in S2PDomainConfig.factors},
                "demo_provenance": {"origin": "s2p_demo_decisions", "planted": True, "evidence_tier": "T_S"},
            }
    return None


def fresh_score(event_id: str, category: str, factors: dict[str, float]) -> dict[str, Any]:
    """A disposable real scorer; no CompoundingScorer/outbox or live-store access."""
    if not demo_presentation_enabled():
        raise HTTPException(status_code=403, detail="S2P demo presentation must be enabled")
    from gae.profile_scorer import ProfileScorer

    preset = S2PPreset()
    scorer = ProfileScorer(mu=np.array(preset.bootstrap_centroids, dtype=float, copy=True),
                           actions=list(preset.shape.action_names),
                           categories=list(preset.shape.category_names), eta_override=0.01)
    vector = [factors[name] for name in S2PDomainConfig.factors]
    result = scorer.score(np.asarray(vector), S2PDomainConfig.get_category_index(category))
    return {
        "event_id": event_id, "category": category, "action": result.action_name,
        "action_name": result.action_name, "action_index": result.action_index,
        "confidence": float(result.confidence), "probabilities": list(result.probabilities),
        "factor_vector": vector, "factor_names": list(S2PDomainConfig.factors),
        "prior_verified_count": 0, "cold_start": True, "decision_id": None,
        "persistent": False, "learning_applied": False, "instance_kind": "disposable_clean_scorer",
        "provenance": {"origin": "demo_day_zero", "planted": True, "evidence_tier": "T_S"},
    }


class RefreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_demo_reseed: Literal[True]


def selected_twin(manager: S2PAutonomyManager) -> FrozenTwin:
    """Production baseline remains immutable, even after demo selection/restart."""
    if not demo_presentation_enabled():
        return manager.twin
    pointer = manager.twin.store.base_dir / "s2p-demo-active.json"
    if not pointer.exists():
        return manager.twin
    selection = json.loads(pointer.read_text(encoding="utf-8"))
    snapshot_id = str(selection["snapshot_id"])
    suffix = snapshot_id.removeprefix("s2p-demo-")
    if not snapshot_id.startswith("s2p-demo-") or len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
        raise ValueError("Invalid demo snapshot selection")
    twin = FrozenTwin(manager.twin.store)
    snapshot = twin.load(snapshot_id)
    if not snapshot.verify_integrity():
        raise ValueError("Demo snapshot integrity check failed")
    return twin


@router.get("/seed-decisions")
def seed_decisions(request: Request) -> dict[str, Any]:
    """Complete resume inventory; the public self listing is capped at 500."""
    require_demo()
    with get_mutation_lock("s2p"):
        rows = request.app.state.scorer.graph_store.get_all_decisions("s2p")
        return {"decisions": rows, "total": len(rows), "complete": True}


@router.post("/learning/re-freeze")
def demo_refreeze(body: RefreezeRequest, request: Request) -> dict[str, Any]:
    require_demo()
    manager = getattr(request.app.state, "s2p_autonomy", None)
    if not isinstance(manager, S2PAutonomyManager):
        raise HTTPException(status_code=503, detail="S2P autonomy state unavailable")
    with get_mutation_lock("s2p"):
        if not manager.twin.is_frozen():
            raise HTTPException(status_code=409, detail="Initialize the production twin first")
        try:
            previous = selected_twin(manager).get_snapshot()
            if not previous.verify_integrity():
                raise ValueError("Previous snapshot integrity check failed")
            run_id = uuid4().hex
            base = manager.twin.store.base_dir
            archive = base / "demo-archive"
            archive.mkdir(parents=True, exist_ok=True)
            with (archive / f"{run_id}.json").open("x", encoding="utf-8") as handle:
                handle.write(previous.to_json())
            provenance = {"origin": "demo_reseed", "archived_previous": True,
                          "previous_checksum": previous.checksum, "archive_id": run_id,
                          "planted": True, "evidence_tier": "T_S"}
            twin = FrozenTwin(manager.twin.store)
            snapshot_id = f"s2p-demo-{run_id}"
            snapshot = twin.freeze(getattr(manager.scorer, "_scorer", manager.scorer),
                                   {**dict(manager.conservation.get_state()), "demo_provenance": provenance},
                                   float(manager.scorer.trajectory().current_iks), snapshot_id)
            temporary = base / f"s2p-demo-active-{run_id}.tmp"
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump({"snapshot_id": snapshot_id}, handle)
            os.replace(temporary, base / "s2p-demo-active.json")
            return {"frozen": True, "snapshot_id": snapshot_id, "checksum": snapshot.checksum,
                    "provenance": provenance}
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            raise HTTPException(status_code=503, detail="Demo re-freeze unavailable; previous selection retained") from exc


@router.post("/learning/persist")
def persist_demo_learning(request: Request) -> dict[str, Any]:
    """Flush the complete live tensor so stale L5 cells cannot undo an override."""
    require_demo()
    with get_mutation_lock("s2p"):
        scorer = request.app.state.scorer
        store = scorer.graph_store
        key = "s2p:demo-runtime-persistence"
        provenance = {"origin": "demo_runtime_flush", "planted": True, "evidence_tier": "T_S"}
        try:
            vectors = {(category, action): np.asarray(scorer.get_centroid(category, action), dtype=float)
                       for category in S2PDomainConfig.categories for action in S2PDomainConfig.actions}
            if any(vector.shape != (len(S2PDomainConfig.factors),) or not np.isfinite(vector).all()
                   for vector in vectors.values()):
                raise ValueError("Invalid live centroid tensor")
            previous = {(row["category"], row["action"]): np.asarray(row["vector_json"], dtype=float)
                        for row in store.get_centroids("s2p")}
            store.save_governance("s2p", key, {"status": "pending", **provenance})
            for (category, action), vector in vectors.items():
                prior = previous.get((category, action), np.zeros_like(vector))
                store.update_centroid(domain="s2p", category=category, action=action,
                    centroid_vector=vector.tolist(), delta_norm=float(np.linalg.norm(vector - prior)),
                    caused_by_decision_id=None)
            persisted = {(row["category"], row["action"]): np.asarray(row["vector_json"], dtype=float)
                         for row in store.get_centroids("s2p")}
            if any(pair not in persisted or not np.array_equal(vector, persisted[pair])
                   for pair, vector in vectors.items()):
                raise ValueError("Incomplete live centroid persistence")
            result = {"status": "persisted", "centroid_cells": len(vectors), "provenance": provenance}
            store.save_governance("s2p", key, result)
            return result
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Demo runtime persistence incomplete; do not restart") from exc


def budget_candidates(scorer: Any, category: str) -> dict[str, Any]:
    """Locate actual live boundaries, including anisotropic/learned kernels."""
    vectors = [np.asarray(scorer.get_centroid(category, action), dtype=float)
               for action in S2PDomainConfig.actions]

    def evaluate(vector: Any) -> tuple[Any, dict[str, float]]:
        factors = dict(zip(S2PDomainConfig.factors, (float(v) for v in vector), strict=True))
        return scorer.score_read_only(factors, category), factors

    trials = [evaluate(vector) for vector in vectors]
    review_trials = []
    for i, left in enumerate(vectors):
        for j, right in enumerate(vectors[i + 1:], start=i + 1):
            lo, hi = left.copy(), right.copy()
            left_action = evaluate(lo)[0].action
            if evaluate(hi)[0].action == left_action:
                continue
            for _ in range(24):
                mid = (lo + hi) / 2
                result, factors = evaluate(mid)
                trials.append((result, factors))
                if i == 0 and j == 1 and result.action == "auto_approve":
                    review_trials.append((result, factors))
                if result.action == left_action:
                    lo = mid
                else:
                    hi = mid
    output = {}
    for profile, chosen in (("easy", max(trials, key=lambda t: t[0].confidence)),
                            ("hard", min(trials, key=lambda t: t[0].confidence))):
        result, factors = chosen
        output[profile] = {"category": category, "factors": factors,
                           "confidence": float(result.confidence), "action": result.action}
    if review_trials:
        # An explicitly synthetic reviewer labels this borderline approval as
        # requiring manual review. The label is not an observed business fact.
        result, factors = min(review_trials, key=lambda t: t[0].confidence)
        output["review"] = {"category": category, "factors": factors,
                            "confidence": float(result.confidence), "action": result.action,
                            "synthetic_review_action": "hold_for_review"}
    return output


@router.get("/budget-candidates")
def get_budget_candidates(request: Request, category: str = "contract_gap") -> dict[str, Any]:
    require_demo()
    if category not in S2PDomainConfig.categories:
        raise HTTPException(status_code=422, detail="Unknown category")
    with get_mutation_lock("s2p"):
        return {"candidates": budget_candidates(request.app.state.scorer, category),
                "provenance": {"origin": "live_boundary_search", "planted": True, "evidence_tier": "T_S"}}
