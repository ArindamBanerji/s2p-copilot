"""Focused acceptance tests for AGE Phase C batch 1."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers.s2p import _held_write_response, _reject_red_write
from app.s2p_graph_status import S2PActiveAGEGraphStore


ROOT = Path(__file__).parents[1]


def _production_sources() -> list[Path]:
    return list((ROOT / "app").rglob("*.py"))


def test_c1_no_legacy_neo4j_or_aura_driver_references() -> None:
    forbidden = ("import neo4j", "from neo4j", "GraphDatabase", "bolt://", "neo4j://")
    hits = [
        f"{path}:{line_number}: {line.strip()}"
        for path in _production_sources()
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(token.lower() in line.lower() for token in forbidden)
    ]
    assert hits == []


def test_c1_active_store_is_the_age_adapter_boundary() -> None:
    assert S2PActiveAGEGraphStore.domain == "s2p"
    source = (ROOT / "app" / "s2p_graph_status.py").read_text(encoding="utf-8")
    assert "create_graph_store" in source
    assert 'backend": "age"' in source


def test_c2_raw_graph_creates_stamp_s2p_domain() -> None:
    checkpoint = (ROOT / "app" / "framework" / "checkpoint.py").read_text(encoding="utf-8")
    intervention = (ROOT / "app" / "framework" / "intervention_controls.py").read_text(
        encoding="utf-8"
    )
    assert "domain:           's2p'" in checkpoint
    assert "domain:       's2p'" in intervention


def test_c2_migration_edge_upsert_adds_domain_stamp() -> None:
    source = (ROOT / "app" / "migration" / "s2p_entity_migration.py").read_text(
        encoding="utf-8"
    )
    assert 'properties.setdefault("domain", S2P_DOMAIN)' in source
    assert 'properties.setdefault("domain_source", MIGRATION_SOURCE)' in source


def test_c2_shadow_updates_remain_domain_scoped() -> None:
    source = (ROOT / "app" / "framework" / "shadow_mode.py").read_text(encoding="utf-8")
    assert "SET d.domain = 's2p'" in source
    assert "WHERE d.domain = 's2p'" in source


def test_c3_red_gate_rejects_before_a_write() -> None:
    with pytest.raises(HTTPException) as raised:
        _reject_red_write({"conservation_status": "RED", "evidence_tier": "T_S"})
    assert getattr(raised.value, "status_code", None) == 503
    assert isinstance(raised.value.detail, dict)
    assert raised.value.detail["gate"] == "BLOCKED"


def test_c3_amber_gate_returns_explicit_held_response() -> None:
    payload = _held_write_response(
        governance={
            "conservation_status": "AMBER",
            "evidence_tier": "T_S",
            "verified_count": 0,
        },
        decision_id="S2P-1",
        outcome="confirm",
    )
    assert payload == {
        "gate": "HELD",
        "learning_applied": False,
        "evidence_tier": "T_S",
        "conservation_status": "AMBER",
        "verified_count": 0,
        "reason": "conservation_amber",
        "decision_id": "S2P-1",
        "outcome": "confirm",
    }


def test_c3_gate_is_before_score_call_in_score_endpoint() -> None:
    source = (ROOT / "app" / "routers" / "s2p.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    score = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "score_procurement_event"
    )
    gate_line = next(
        node.lineno
        for node in ast.walk(score)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_score_write_governance"
    )
    scorer_call_line = next(
        node.lineno
        for node in ast.walk(score)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "score"
    )
    assert gate_line < scorer_call_line
