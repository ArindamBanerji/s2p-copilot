import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient

from app.main import app
from copilot_sdk.enterprise.process_ingest import ProcessExportIngester


client = TestClient(app)

EXPORT_EVENTS = [
    {
        "case_id": "INV-1",
        "activity": "3-way match",
        "timestamp": "2026-07-01T08:00:00Z",
        "resource": "Chicago AP team",
        "duration_ms": 15120000,
        "variant": "non-standard format",
        "supplier": "Supplier X",
    },
    {
        "case_id": "INV-2",
        "activity": "3-way match",
        "timestamp": "2026-07-01T09:00:00Z",
        "resource": "Chicago AP team",
        "duration_ms": 15120000,
        "variant": "non-standard format",
        "supplier": "Supplier Y",
    },
    {
        "case_id": "INV-3",
        "activity": "invoice receipt",
        "timestamp": "2026-07-01T10:00:00Z",
        "resource": "Houston AP team",
        "duration_ms": 3960000,
        "variant": "standard",
        "supplier": "Supplier Z",
    },
]


def test_ingest_process_export_basic():
    summary = ProcessExportIngester().ingest(EXPORT_EVENTS)

    assert summary["cases_ingested"] == 3
    assert summary["activities_found"] == 2
    assert summary["provenance"] == "scraped_external"
    assert summary["context_graph"]["relationships"]


def test_ingest_bottleneck_detection():
    summary = ProcessExportIngester().ingest(EXPORT_EVENTS)

    assert summary["bottleneck_activities"][0]["activity"] == "3-way match"
    assert summary["bottleneck_activities"][0]["resource"] == "Chicago AP team"


def test_fusion_endpoint_returns_200():
    response = client.post("/api/s2p/enterprise/process-fusion", json=EXPORT_EVENTS)

    assert response.status_code == 200


def test_fusion_where_what_why_which_present():
    payload = client.post("/api/s2p/enterprise/process-fusion", json=EXPORT_EVENTS).json()

    assert {"where", "what", "why", "which_decision"} <= set(payload)
    assert payload["where"]["bottleneck"] == "Chicago AP team"
    assert payload["which_decision"]["recommendation"]


def test_fusion_impact_has_sample_provenance():
    payload = client.post("/api/s2p/enterprise/process-fusion", json=EXPORT_EVENTS).json()

    assert payload["which_decision"]["estimated_impact"] == "$6,720/year"
    assert payload["which_decision"]["bottleneck_cases"] == 2
    assert payload["which_decision"]["annual_projection"] == 168.0
    assert payload["which_decision"]["provenance"] == "sample"
    assert payload["which_decision"]["computation"] == "bottleneck_count x analyst_cost x annualization"


def test_fusion_empty_input_returns_zero_impact():
    payload = client.post("/api/s2p/enterprise/process-fusion", json=[]).json()

    assert payload["which_decision"]["estimated_impact"] == "$0/year"
    assert payload["which_decision"]["bottleneck_cases"] == 0
    assert payload["which_decision"]["note"] == "No process events ingested."


def test_fusion_no_write_back_claim():
    payload = client.post("/api/s2p/enterprise/process-fusion", json=EXPORT_EVENTS).json()
    text = str(payload).lower()

    assert "write-back" not in text
    assert "write back" not in text
    assert "auto-implement" not in text
