import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app


client = TestClient(app)


def test_discovery_alerts_returns_200():
    response = client.get("/api/s2p/discovery/alerts")

    assert response.status_code == 200


def test_discovery_alerts_response_shape():
    data = client.get("/api/s2p/discovery/alerts").json()

    assert {"discoveries", "total_discoveries", "sources_connected", "highest_impact"} <= set(data)
    assert {
        "discovery_id",
        "title",
        "sources",
        "correlation_strength",
        "impact_estimate",
        "pattern",
        "confidence",
        "discovered_at",
        "recommendation",
    } <= set(data["discoveries"][0])


def test_discovery_count_is_3():
    data = client.get("/api/s2p/discovery/alerts").json()

    assert data["total_discoveries"] == 3
    assert len(data["discoveries"]) == 3


def test_yangtze_discovery_highest_impact():
    data = client.get("/api/s2p/discovery/alerts").json()
    top = data["discoveries"][0]

    assert top["title"] == "Price increase risk at Yangtze Raw Materials"
    assert top["impact_estimate"] == "$420K exposure in 6 weeks"
    assert top["correlation_strength"] == 0.89


def test_discovery_has_sources():
    data = client.get("/api/s2p/discovery/alerts").json()
    yangtze = data["discoveries"][0]

    assert {"Celonis", "D&B"}.issubset(set(yangtze["sources"]))
    assert any("Commodity" in source for source in yangtze["sources"])


def test_discovery_correlation_in_range():
    data = client.get("/api/s2p/discovery/alerts").json()

    assert all(0.0 <= row["correlation_strength"] <= 1.0 for row in data["discoveries"])
    assert all(0.0 <= row["confidence"] <= 1.0 for row in data["discoveries"])


def test_discovery_has_recommendation():
    data = client.get("/api/s2p/discovery/alerts").json()

    assert all(row["recommendation"] for row in data["discoveries"])
    assert "lock pricing" in data["discoveries"][0]["recommendation"].lower()
    assert "backup" in data["discoveries"][0]["recommendation"].lower()


def test_discoveries_sorted_by_impact():
    data = client.get("/api/s2p/discovery/alerts").json()
    strengths = [row["correlation_strength"] for row in data["discoveries"]]

    assert strengths == sorted(strengths, reverse=True)


def test_disruptions_returns_200():
    response = client.get("/api/s2p/discovery/disruptions")

    assert response.status_code == 200


def test_disruptions_response_shape():
    data = client.get("/api/s2p/discovery/disruptions").json()

    assert {
        "disruptions",
        "total_disruptions",
        "cumulative_savings",
        "avg_improvement_pct",
        "learning_narrative",
    } <= set(data)
    assert {
        "disruption_id",
        "disruption_type",
        "occurrence",
        "recovery_time_days",
        "recovery_cost",
        "improvement_from_first",
        "pattern_reuse",
        "decisions_applied",
    } <= set(data["disruptions"][0])


def test_disruption_count_is_3():
    data = client.get("/api/s2p/discovery/disruptions").json()

    assert data["total_disruptions"] == 3
    assert len(data["disruptions"]) == 3


def test_first_occurrence_longest_recovery():
    disruptions = client.get("/api/s2p/discovery/disruptions").json()["disruptions"]
    first = next(row for row in disruptions if row["occurrence"] == 1)

    assert first["recovery_time_days"] == max(row["recovery_time_days"] for row in disruptions)
    assert first["recovery_cost"] == 15_000_000
    assert first["pattern_reuse"] == "none"


def test_recovery_time_decreases():
    disruptions = client.get("/api/s2p/discovery/disruptions").json()["disruptions"]
    recovery_days = [row["recovery_time_days"] for row in disruptions]

    assert recovery_days == sorted(recovery_days, reverse=True)


def test_improvement_pct_increases():
    disruptions = client.get("/api/s2p/discovery/disruptions").json()["disruptions"]
    improvements = [row["improvement_from_first"] for row in disruptions]

    assert improvements == sorted(improvements)
    assert improvements[1] == 0.84
    assert improvements[2] == 0.97


def test_cumulative_savings_positive():
    data = client.get("/api/s2p/discovery/disruptions").json()

    assert data["cumulative_savings"] == 27_500_000.0
    assert data["cumulative_savings"] > 0


def test_learning_narrative_populated():
    data = client.get("/api/s2p/discovery/disruptions").json()

    assert "centroids accumulated" in data["learning_narrative"]
    assert "disruption-response patterns" in data["learning_narrative"]


def test_discovery_router_mounted():
    assert client.get("/api/s2p/discovery/alerts").status_code == 200
    assert client.get("/api/s2p/discovery/disruptions").status_code == 200
