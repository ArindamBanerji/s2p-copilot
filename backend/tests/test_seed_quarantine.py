"""Contract tests for safe, domain-stamped S2P seed data."""

from __future__ import annotations

import pytest
import os

from app.seed_graph import seed_graph, seed_s2p_graph


def test_seed_rejects_soc_graph_without_flag() -> None:
    previous = os.environ.pop("ALLOW_PRODUCTION_SEED", None)
    try:
        with pytest.raises(ValueError, match="Cannot seed production graph"):
            seed_graph(graph="soc_graph")
    finally:
        if previous is not None:
            os.environ["ALLOW_PRODUCTION_SEED"] = previous


def test_seed_allows_disposable_test_graph() -> None:
    nodes, edges = seed_graph(graph="test_scratch")

    assert nodes
    assert edges


def test_seed_stamps_domain_and_provenance() -> None:
    nodes, _ = seed_s2p_graph(seed=42)
    decisions = [node for node in nodes if node["label"] == "Decision"]

    assert decisions
    assert all(node["properties"]["domain"] == "s2p" for node in decisions)
    assert all(node["properties"]["provenance"] == "seed" for node in decisions)


def test_seed_idempotent_rerun() -> None:
    first = seed_s2p_graph(seed=42)
    second = seed_s2p_graph(seed=42)

    assert first == second
