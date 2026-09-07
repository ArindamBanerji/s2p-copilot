"""Regression coverage for SDK-driven S2P demo preseed."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_preseed_module() -> ModuleType:
    workspace = Path(__file__).resolve().parents[3]
    path = workspace / "copilot-sdk" / "scripts" / "preseed_all_copilots.py"
    spec = importlib.util.spec_from_file_location("slot_j_preseed_all_copilots", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load preseed script at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preseed_all_copilots_still_seeds_s2p_score_and_learn(monkeypatch) -> None:
    preseed = _load_preseed_module()
    posts: list[tuple[str, dict[str, Any]]] = []

    def api_post(_base_url: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        posts.append((path, body))
        if path == "/api/s2p/score":
            return {"decision_id": body["event_id"], "action": "auto_approve"}
        if path == "/api/learn":
            return {"reward": 1.0}
        raise AssertionError(path)

    monkeypatch.setattr(preseed, "S2P_PRESEED_DECISIONS", 3)
    monkeypatch.setattr(preseed, "check_health", lambda _base_url: (True, {"ok": True}))
    monkeypatch.setattr(preseed, "check_already_seeded", lambda _base_url: (False, {"decisions_total": 0}))
    monkeypatch.setattr(preseed, "api_post", api_post)

    result = preseed.seed_s2p_domain(argparse.Namespace(force=False, dry_run=False))

    assert result.name == "s2p"
    assert result.successes == 3
    assert result.failures == 0
    assert [path for path, _body in posts] == [
        "/api/s2p/score",
        "/api/learn",
        "/api/s2p/score",
        "/api/learn",
        "/api/s2p/score",
        "/api/learn",
    ]
    learn_payloads = [body for path, body in posts if path == "/api/learn"]
    assert {body["context"]["seed_domain"] for body in learn_payloads} == {"s2p"}
