"""Cross-boundary checks for S2P's domain-bound graph reader."""

from app.graph.s2p_graph_reader import S2PGraphReader
from copilot_sdk.graph.memory_store import InMemoryGraphStore


def test_s2p_scorer_passes_domain_to_get_decision() -> None:
    """The S2P read facade supplies domain to the required store contract."""
    store = InMemoryGraphStore(domain="s2p")
    decision_id = store.write_decision(
        domain="s2p",
        category="invoice",
        action="approve",
        confidence=0.9,
        factors={"amount": 0.2},
        metadata={"decision_id": "S2P-DOMAIN-TEST"},
    )
    reader = S2PGraphReader(store)

    decision = reader.get_decision(decision_id)

    assert decision is not None
    assert decision["domain"] == "s2p"
