"""
LearningState singleton for CopilotFramework.
Domain layer (SOC/S2P) builds the profile and W_init,
passes them to init_learning_state().
Safe to copy to copilot-sdk.

Design: stateless utility functions.
The domain layer (services/gae_state.py) owns the module-level singleton
state so it can be patched in tests and managed within process context.
Framework provides the serialization/deserialization LOGIC; the caller
provides the state_path and profile at every call site.

Functions
---------
make_state()             — create a LearningState from raw params
load_from_file()         — deserialize W + history from JSON checkpoint
read_checkpoint_metadata() — read checkpoint metadata block
save_state()             — atomically persist W + history to JSON
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
from gae.learning import LearningState, WeightUpdate, CalibrationProfile
from gae import BootstrapResult  # noqa: F401 — re-exported for callers

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure utility functions (stateless — no module-level singletons)
# ---------------------------------------------------------------------------

def make_state(
    W: np.ndarray,
    factor_names: list,
    profile: CalibrationProfile,
    decision_count: int = 0,
) -> LearningState:
    """Create a fresh LearningState from raw parameters."""
    n_actions, n_factors = W.shape
    return LearningState(
        W=W.copy(),
        n_actions=n_actions,
        n_factors=n_factors,
        factor_names=factor_names,
        profile=profile,
        decision_count=decision_count,
    )


def load_from_file(state_path: Path, profile: CalibrationProfile) -> LearningState:
    """
    Deserialize W matrix and WeightUpdate history from a JSON checkpoint.

    Parameters
    ----------
    state_path : Path to the checkpoint JSON file (must exist).
    profile    : CalibrationProfile used to reconstruct the LearningState.
                 Pass the domain-calibrated profile (e.g. _soc_profile()).
    """
    with open(state_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    W = np.array(data["W"], dtype=np.float64)
    n_a = data["n_actions"]
    n_f = data["n_factors"]
    state = LearningState(
        W=W,
        n_actions=n_a,
        n_factors=n_f,
        factor_names=data["factor_names"],
        decision_count=data.get("decision_count", 0),
        profile=profile,
    )
    history = []
    for h in data.get("history", []):
        try:
            wu = WeightUpdate(
                decision_number=        h["decision_number"],
                timestamp=              h["timestamp"],
                action_index=           h["action_index"],
                action_name=            h["action_name"],
                outcome=                h["outcome"],
                factor_vector=          np.array(h["factor_vector"], dtype=np.float64),
                delta_applied=          np.array(h["delta_applied"], dtype=np.float64),
                W_before=               np.zeros((n_a, n_f), dtype=np.float64),
                W_after=                np.array(h["W_after"], dtype=np.float64),
                alpha_effective=        h["alpha_effective"],
                confidence_at_decision= h["confidence_at_decision"],
            )
            history.append(wu)
        except Exception as exc:
            log.warning("[GAE] Skipping malformed history entry: %s", exc)
    state.history = history
    return state


def read_checkpoint_metadata(state_path: Path) -> dict:
    """Read the metadata field from the checkpoint. Returns {} if absent."""
    with open(state_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("metadata", {})


def save_state(
    learning_state: LearningState,
    bootstrap_metadata: Optional[dict],
    state_path: Path,
) -> None:
    """
    Atomically persist W matrix + WeightUpdate history to a JSON checkpoint.

    Uses a temp-file + rename strategy to prevent partial writes on crash.
    No-op if learning_state is None.
    """
    if learning_state is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    history_data = []
    for wu in learning_state.history:
        history_data.append({
            "decision_number":        wu.decision_number,
            "timestamp":              wu.timestamp,
            "action_index":           wu.action_index,
            "action_name":            wu.action_name,
            "outcome":                wu.outcome,
            "alpha_effective":        wu.alpha_effective,
            "confidence_at_decision": wu.confidence_at_decision,
            "factor_vector":          wu.factor_vector.tolist(),
            "delta_applied":          wu.delta_applied.tolist(),
            "W_after":                wu.W_after.tolist(),
        })
    payload = {
        "W":             learning_state.W.tolist(),
        "n_actions":     learning_state.n_actions,
        "n_factors":     learning_state.n_factors,
        "factor_names":  learning_state.factor_names,
        "decision_count": learning_state.decision_count,
        "history":       history_data,
    }
    if bootstrap_metadata:
        payload["metadata"] = bootstrap_metadata
    fd, tmp = tempfile.mkstemp(
        dir=state_path.parent, suffix=".tmp", prefix=".gae_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, state_path)
        log.debug(
            "[GAE] State saved to %s (step=%d)",
            state_path, learning_state.decision_count,
        )
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
