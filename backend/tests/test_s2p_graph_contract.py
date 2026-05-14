from app.graph_contract import S2P_GRAPH_CONTRACT
from app.seed_graph import seed_s2p_graph


FORBIDDEN_SOC_TERMS = (
    "credential_access",
    "lateral_movement",
    "malware",
    "threat_intel",
    "data_exfiltration",
)


def test_contract_validates():
    assert S2P_GRAPH_CONTRACT.validate() == []


def test_contract_has_required_decision_surface():
    labels = {node.label for node in S2P_GRAPH_CONTRACT.node_types}
    edge_labels = {edge.label for edge in S2P_GRAPH_CONTRACT.edge_types}

    assert "Decision" in labels
    assert "DECIDED_ON" in edge_labels


def test_contract_graph_name_is_s2p_only():
    assert S2P_GRAPH_CONTRACT.graph_name == "s2p_graph"
    assert S2P_GRAPH_CONTRACT.graph_name not in {"trading_graph", "purchasing_graph", "dataops_graph"}


def test_contract_has_fixture_domain_nodes():
    labels = {node.label for node in S2P_GRAPH_CONTRACT.node_types}

    assert {"Invoice", "Supplier", "ProcessModel", "Activity"}.issubset(labels)


def test_seed_is_deterministic_for_same_seed():
    assert seed_s2p_graph(seed=42) == seed_s2p_graph(seed=42)


def test_seed_nodes_have_required_shape():
    nodes, _ = seed_s2p_graph(seed=42)

    assert nodes
    for node in nodes:
        assert node["id"]
        assert node["label"]
        assert isinstance(node["properties"], dict)


def test_seed_edges_have_required_shape():
    _, edges = seed_s2p_graph(seed=42)

    assert edges
    for edge in edges:
        assert edge["label"]
        assert edge["from_id"]
        assert edge["to_id"]
        assert isinstance(edge.get("properties", {}), dict)


def test_seed_edges_reference_seeded_node_ids():
    nodes, edges = seed_s2p_graph(seed=42)
    node_ids = {node["id"] for node in nodes}

    assert edges
    for edge in edges:
        assert edge["from_id"] in node_ids
        assert edge["to_id"] in node_ids


def test_seed_contains_every_contract_node_label():
    nodes, _ = seed_s2p_graph(seed=42)
    seeded_labels = {node["label"] for node in nodes}
    contract_labels = {node.label for node in S2P_GRAPH_CONTRACT.node_types}

    assert contract_labels.issubset(seeded_labels)


def test_seed_contains_every_contract_edge_label():
    _, edges = seed_s2p_graph(seed=42)
    seeded_labels = {edge["label"] for edge in edges}
    contract_labels = {edge.label for edge in S2P_GRAPH_CONTRACT.edge_types}

    assert contract_labels.issubset(seeded_labels)


def test_seed_expected_counts_match_contract():
    nodes, edges = seed_s2p_graph(seed=42)

    assert len(nodes) == S2P_GRAPH_CONTRACT.expected_nodes
    assert len(edges) == S2P_GRAPH_CONTRACT.expected_edges


def test_no_soc_vocabulary_in_contract_or_seed():
    nodes, edges = seed_s2p_graph(seed=42)
    text = " ".join(
        [
            S2P_GRAPH_CONTRACT.graph_name,
            *(node.label for node in S2P_GRAPH_CONTRACT.node_types),
            *(edge.label for edge in S2P_GRAPH_CONTRACT.edge_types),
            *(str(node) for node in nodes),
            *(str(edge) for edge in edges),
        ]
    ).lower()

    for forbidden in FORBIDDEN_SOC_TERMS:
        assert forbidden not in text
