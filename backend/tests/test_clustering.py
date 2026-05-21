import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app
from app.routers import s2p_clustering

client = TestClient(app)


def test_clusters_returns_200():
    response = client.get("/api/s2p/suppliers/clusters")

    assert response.status_code == 200


def test_clusters_response_has_required_fields():
    data = client.get("/api/s2p/suppliers/clusters").json()

    assert {"clusters", "total_suppliers", "consolidation_candidates", "estimated_annual_savings", "method"} <= set(data)
    assert data["method"] == "behavioral_centroid"
    assert {
        "cluster_id",
        "label",
        "members",
        "centroid",
        "consolidation_potential",
        "estimated_savings",
    } <= set(data["clusters"][0])


def test_clusters_contains_all_suppliers():
    data = client.get("/api/s2p/suppliers/clusters").json()
    supplier_ids = {supplier["supplier_id"] for supplier in client.get("/api/s2p/suppliers").json()["suppliers"]}
    clustered = {supplier_id for cluster in data["clusters"] for supplier_id in cluster["members"]}

    assert clustered == supplier_ids
    assert data["total_suppliers"] == len(supplier_ids)


def test_clusters_have_valid_consolidation_potential():
    data = client.get("/api/s2p/suppliers/clusters").json()

    assert {cluster["consolidation_potential"] for cluster in data["clusters"]} <= {"high", "medium", "low"}


def test_high_potential_cluster_has_savings():
    data = client.get("/api/s2p/suppliers/clusters").json()
    budget = next(cluster for cluster in data["clusters"] if cluster["label"] == "Budget Volatile")

    assert budget["consolidation_potential"] == "high"
    assert budget["estimated_savings"] == 2_400_000.0
    assert data["estimated_annual_savings"] == sum(cluster["estimated_savings"] for cluster in data["clusters"])


def test_cluster_members_are_unique_across_clusters():
    data = client.get("/api/s2p/suppliers/clusters").json()
    members = [supplier_id for cluster in data["clusters"] for supplier_id in cluster["members"]]

    assert len(members) == len(set(members))


def test_similarity_returns_top_5():
    data = client.get("/api/s2p/suppliers/similarity", params={"supplier_id": "SUP-001"}).json()

    assert data["supplier_id"] == "SUP-001"
    assert data["method"] == "cosine_distance"
    assert len(data["similar_suppliers"]) == 5
    assert all(row["supplier_id"] != "SUP-001" for row in data["similar_suppliers"])


def test_similarity_unknown_supplier_returns_empty():
    data = client.get("/api/s2p/suppliers/similarity", params={"supplier_id": "UNKNOWN"}).json()

    assert data == {"supplier_id": "UNKNOWN", "similar_suppliers": [], "method": "cosine_distance"}


def test_similarity_distances_are_sorted():
    data = client.get("/api/s2p/suppliers/similarity", params={"supplier_id": "SUP-001"}).json()
    distances = [row["distance"] for row in data["similar_suppliers"]]

    assert distances == sorted(distances)


def test_behavioral_vector_extraction():
    profile = {
        "otif": 0.88,
        "exception_rate": 0.12,
        "pricing_trend": None,
        "avg_invoice_amount": 18_000.0,
        "payment_terms": "Net 45",
    }

    vector = s2p_clustering._supplier_behavior_vector(profile)

    assert len(vector) == 5
    assert vector[0] == 0.88
    assert vector[1] == 0.12
    assert vector[3] == 0.88
    assert vector[4] == 0.55


def test_cosine_distance_computation():
    assert s2p_clustering._cosine_distance([1.0, 0.0], [1.0, 0.0]) == 0.0
    assert s2p_clustering._cosine_distance([0.0, 0.0], [1.0, 0.0]) == 1.0


def test_cluster_label_assignment():
    profiles = s2p_clustering._load_supplier_profiles()
    clusters = s2p_clustering._build_demo_clusters(profiles)
    by_label = {cluster["label"]: set(cluster["members"]) for cluster in clusters}

    assert by_label["Reliable Premium"] == {"SUP-003", "SUP-004", "SUP-006"}
    assert by_label["Budget Volatile"] == {"SUP-001", "SUP-005", "SUP-009"}
    assert by_label["Mid-Tier Consistent"] == {"SUP-002", "SUP-007"}
    assert by_label["Niche Specialist"] == {"SUP-008", "SUP-010"}


def test_consolidation_potential_threshold():
    assert s2p_clustering._cluster_potential({"label": "Budget Volatile", "members": ["A", "B", "C"]}) == "high"
    assert s2p_clustering._cluster_potential({"label": "Reliable Premium", "members": ["A", "B", "C"]}) == "medium"
    assert s2p_clustering._cluster_potential({"label": "Niche Specialist", "members": ["A", "B"]}) == "low"


def test_savings_estimation():
    assert s2p_clustering._estimate_savings(
        {"label": "Budget Volatile", "members": ["A"], "consolidation_potential": "high"}
    ) == 2_400_000.0
    assert s2p_clustering._estimate_savings(
        {"label": "Reliable Premium", "members": ["A", "B", "C"], "consolidation_potential": "medium"}
    ) == 450_000.0


def test_all_demo_suppliers_assigned_to_cluster():
    profiles = s2p_clustering._load_supplier_profiles()
    clusters = s2p_clustering._build_demo_clusters(profiles)
    assigned = {supplier_id for cluster in clusters for supplier_id in cluster["members"]}

    assert assigned == {profile["supplier_id"] for profile in profiles}


def test_static_cluster_routes_not_shadowed_by_supplier_id_route():
    clusters = client.get("/api/s2p/suppliers/clusters")
    similarity = client.get("/api/s2p/suppliers/similarity", params={"supplier_id": "SUP-001"})

    assert clusters.status_code == 200
    assert "clusters" in clusters.json()
    assert similarity.status_code == 200
    assert "similar_suppliers" in similarity.json()
