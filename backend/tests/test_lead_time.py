import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.main import app
from app.routers import lead_time_router
from app.services.lead_time import (
    compute_actual_lead_time_days,
    compute_lead_time_result,
    compute_lead_times,
    detect_trend,
    parse_date,
)


client = TestClient(app)


def _invoice(
    invoice_id="INV-1",
    supplier_id="SUP-1",
    supplier_name="Supplier 1",
    category="price_variance",
    po_date="2026-01-01",
    gr_date="2026-01-11",
    contractual=8,
    season="Q1",
    volume_band="medium",
):
    return {
        "invoice_id": invoice_id,
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "category": category,
        "metadata": {
            "po_date": po_date,
            "gr_date": gr_date,
            "contractual_lead_time_days": contractual,
            "season": season,
            "volume_band": volume_band,
        },
    }


def _synthetic_invoices():
    path = Path(__file__).resolve().parents[2] / "data" / "synthetic_invoices.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _actual_delta(invoice):
    metadata = invoice["metadata"]
    actual = (
        date.fromisoformat(metadata["gr_date"])
        - date.fromisoformat(metadata["po_date"])
    ).days
    return actual - float(metadata["contractual_lead_time_days"])


def _supplier_average_deltas(invoices):
    deltas = defaultdict(list)
    for invoice in invoices:
        deltas[invoice["supplier_id"]].append(_actual_delta(invoice))
    return {
        supplier_id: sum(values) / len(values)
        for supplier_id, values in deltas.items()
    }


def test_compute_basic_lead_time():
    assert compute_actual_lead_time_days(_invoice()) == 10.0


def test_compute_per_supplier():
    stats = compute_lead_times([
        _invoice(supplier_id="SUP-1"),
        _invoice(invoice_id="INV-2", supplier_id="SUP-2"),
    ])

    assert {stat.supplier_id for stat in stats} == {"SUP-1", "SUP-2"}


def test_compute_per_category():
    stats = compute_lead_times([
        _invoice(category="price_variance"),
        _invoice(invoice_id="INV-2", category="contract_gap"),
    ])

    assert {stat.category for stat in stats} == {"price_variance", "contract_gap"}


def test_compute_per_season():
    stats = compute_lead_times([
        _invoice(season="Q1"),
        _invoice(invoice_id="INV-2", season="Q4"),
    ])

    assert {stat.season for stat in stats} == {"Q1", "Q4"}


def test_compute_per_volume_band():
    stats = compute_lead_times([
        _invoice(volume_band="low"),
        _invoice(invoice_id="INV-2", volume_band="high"),
    ])

    assert {stat.volume_band for stat in stats} == {"low", "high"}


def test_missing_dates_skipped():
    result = compute_lead_time_result([_invoice(po_date=None)])

    assert result.stats == []
    assert result.missing_timestamp_count == 1


def test_negative_lead_time_skipped():
    result = compute_lead_time_result([_invoice(po_date="2026-01-12", gr_date="2026-01-11")])

    assert result.stats == []
    assert result.skipped_negative_count == 1


def test_on_time_percentage():
    stats = compute_lead_times([
        _invoice(invoice_id="INV-1", gr_date="2026-01-10", contractual=8),
        _invoice(invoice_id="INV-2", gr_date="2026-01-20", contractual=8),
    ], tolerance_days=3.0)

    assert stats[0].on_time_pct == 0.5


def test_p95_small_sample():
    stats = compute_lead_times([
        _invoice(invoice_id="INV-1", gr_date="2026-01-02", contractual=10),
        _invoice(invoice_id="INV-2", gr_date="2026-01-03", contractual=10),
    ])

    assert stats[0].actual_p95_days == 2.0


def test_std_definition():
    stats = compute_lead_times([
        _invoice(invoice_id="INV-1", gr_date="2026-01-11", contractual=10),
        _invoice(invoice_id="INV-2", gr_date="2026-01-13", contractual=10),
        _invoice(invoice_id="INV-3", gr_date="2026-01-15", contractual=10),
    ])

    assert stats[0].actual_std_days == 1.63


def test_trend_deteriorating():
    assert detect_trend([10.0] * 12 + [15.0] * 5, window=5) == "deteriorating"


def test_trend_improving():
    assert detect_trend([15.0] * 12 + [10.0] * 5, window=5) == "improving"


def test_trend_stable():
    assert detect_trend([10.0, 11.0] * 10, window=5) == "stable"


def test_empty_invoices():
    result = compute_lead_time_result([])

    assert result.stats == []
    assert result.missing_timestamp_count == 0


def test_supplier_filter():
    stats = compute_lead_times([
        _invoice(supplier_id="SUP-1"),
        _invoice(invoice_id="INV-2", supplier_id="SUP-2"),
    ], supplier_id="SUP-2")

    assert len(stats) == 1
    assert stats[0].supplier_id == "SUP-2"


def test_invalid_dates_do_not_crash():
    result = compute_lead_time_result([_invoice(po_date="not-a-date")])

    assert result.stats == []
    assert result.missing_timestamp_count == 1
    assert parse_date("2026-01-01T12:00:00Z").isoformat() == "2026-01-01"


def test_alert_threshold():
    stats = compute_lead_times([_invoice(contractual=5, gr_date="2026-01-15")], tolerance_days=3.0)

    assert stats[0].alert is True


def test_alert_level_thresholds():
    watch = compute_lead_times([_invoice(contractual=10, gr_date="2026-01-15")], tolerance_days=3.0)[0]
    elevated = compute_lead_times([_invoice(contractual=8, gr_date="2026-01-17")], tolerance_days=3.0)[0]
    critical = compute_lead_times([_invoice(contractual=5, gr_date="2026-01-18")], tolerance_days=3.0)[0]

    assert watch.alert_level == "watch"
    assert elevated.alert_level == "elevated"
    assert critical.alert_level == "critical"


def test_synthetic_invoices_have_po_gr_contractual_fields():
    for invoice in _synthetic_invoices():
        metadata = invoice["metadata"]
        assert metadata["po_date"]
        assert metadata["gr_date"]
        assert metadata["contractual_lead_time_days"] is not None


def test_synthetic_dates_parse_and_non_negative_lead_time():
    for invoice in _synthetic_invoices():
        assert parse_date(invoice["metadata"]["po_date"]) is not None
        assert parse_date(invoice["metadata"]["gr_date"]) is not None
        assert compute_actual_lead_time_days(invoice) >= 0


def test_synthetic_generation_has_season_and_volume_band():
    invoices = _synthetic_invoices()
    seasons = {invoice["metadata"]["season"] for invoice in invoices}
    volumes = {invoice["metadata"]["volume_band"] for invoice in invoices}

    assert seasons == {"Q1", "Q2", "Q3", "Q4"}
    assert volumes == {"low", "medium", "high"}


def test_synthetic_invoices_cover_all_four_seasons():
    invoices = _synthetic_invoices()
    counts = defaultdict(int)
    for invoice in invoices:
        counts[invoice["metadata"]["season"]] += 1

    assert set(counts) == {"Q1", "Q2", "Q3", "Q4"}
    assert counts["Q4"] >= 5


def test_synthetic_q4_degradation_is_exercised():
    q4_degraded = [
        invoice
        for invoice in _synthetic_invoices()
        if invoice["metadata"]["season"] == "Q4" and _actual_delta(invoice) > 3.0
    ]

    assert q4_degraded
    assert {invoice["supplier_id"] for invoice in q4_degraded} & {"SUP-001", "SUP-005", "SUP-009"}


def test_reliable_suppliers_near_contractual_lead_time():
    averages = _supplier_average_deltas(_synthetic_invoices())

    reliable_suppliers = {"SUP-003", "SUP-004", "SUP-006", "SUP-008", "SUP-010"}
    assert all(abs(averages[supplier_id]) <= 2.0 for supplier_id in reliable_suppliers)


def test_unreliable_or_declining_suppliers_show_larger_lead_time_delta():
    averages = _supplier_average_deltas(_synthetic_invoices())
    reliable = ["SUP-003", "SUP-004", "SUP-006", "SUP-008", "SUP-010"]
    unreliable = ["SUP-001", "SUP-005", "SUP-009"]

    reliable_average = sum(averages[supplier_id] for supplier_id in reliable) / len(reliable)
    unreliable_average = sum(averages[supplier_id] for supplier_id in unreliable) / len(unreliable)

    assert unreliable_average > reliable_average + 4.0


def test_fixture_invoice_count_preserved_if_baseline_count_available():
    assert len(_synthetic_invoices()) == 50


def test_representative_synthetic_record_is_deterministic():
    invoice = _synthetic_invoices()[0]

    assert invoice["invoice_id"] == "S2P-INV-0001"
    assert invoice["metadata"]["po_date"] == "2025-12-12"
    assert invoice["metadata"]["gr_date"] == "2026-01-06"
    assert invoice["metadata"]["contractual_lead_time_days"] == 20.0
    assert invoice["metadata"]["season"] == "Q1"
    assert invoice["metadata"]["volume_band"] == "medium"


def test_summary_endpoint():
    response = client.get("/api/s2p/lead-time/summary")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["stats"], list)
    assert data["total_samples"] > 0


def test_lead_time_response_has_provenance():
    response = client.get("/api/s2p/lead-time/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["provenance"] == "sample"
    assert data["source"] == "synthetic_invoices.json"


def test_summary_endpoint_supplier_filter():
    response = client.get("/api/s2p/lead-time/summary", params={"supplier_id": "SUP-001"})

    assert response.status_code == 200
    assert {stat["supplier_id"] for stat in response.json()["stats"]} <= {"SUP-001"}


def test_suppliers_endpoint():
    response = client.get("/api/s2p/lead-time/suppliers")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["suppliers"], list)
    assert data["total_suppliers"] == len(data["suppliers"])
    assert data["provenance"] == "sample"
    assert data["source"] == "synthetic_invoices.json"
    assert all(row["provenance"] == "sample" for row in data["suppliers"])


def test_supplier_endpoint():
    response = client.get("/api/s2p/lead-time/suppliers/SUP-001")

    assert response.status_code == 200
    data = response.json()
    assert data["supplier_id"] == "SUP-001"
    assert isinstance(data["stats"], list)
    assert data["provenance"] == "sample"
    assert data["source"] == "synthetic_invoices.json"


def test_supplier_endpoint_unknown_supplier_404():
    response = client.get("/api/s2p/lead-time/suppliers/UNKNOWN_SUPPLIER")

    assert response.status_code == 404


def test_supplier_endpoint_known_supplier_no_valid_samples_safe_if_constructible(monkeypatch):
    monkeypatch.setattr(lead_time_router, "load_suppliers", lambda: [{"supplier_id": "SUP-X", "name": "Supplier X"}])
    monkeypatch.setattr(lead_time_router, "load_invoices", lambda: [_invoice(supplier_id="SUP-X", po_date=None)])

    response = client.get("/api/s2p/lead-time/suppliers/SUP-X")

    assert response.status_code == 200
    assert response.json()["stats"] == []
    assert response.json()["warnings"]


def test_alerts_endpoint():
    response = client.get("/api/s2p/lead-time/alerts")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["alerts"], list)
    assert data["total_alerts"] == len(data["alerts"])
    assert data["provenance"] == "sample"
    assert data["source"] == "synthetic_invoices.json"


def test_alerts_tolerance_param():
    strict = client.get("/api/s2p/lead-time/alerts", params={"tolerance_days": 0}).json()
    relaxed = client.get("/api/s2p/lead-time/alerts", params={"tolerance_days": 100}).json()

    assert strict["total_alerts"] >= relaxed["total_alerts"]
    assert relaxed["tolerance_days"] == 100.0


def test_route_table_no_collision():
    routes = {
        (route.path, ",".join(sorted(route.methods)))
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/s2p/lead-time")
    }

    assert ("/api/s2p/lead-time/summary", "GET") in routes
    assert ("/api/s2p/lead-time/alerts", "GET") in routes
    assert ("/api/s2p/lead-time/suppliers", "GET") in routes
    assert ("/api/s2p/lead-time/suppliers/{supplier_id}", "GET") in routes
    assert client.get("/api/s2p/lead-time/summary").status_code == 200
    assert client.get("/api/s2p/lead-time/alerts").status_code == 200


def test_response_models_are_json_safe():
    response = client.get("/api/s2p/lead-time/summary")

    assert response.status_code == 200
    json.dumps(response.json())
    endpoints = {
        (route.path, route.name): route
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/s2p/lead-time")
    }
    assert endpoints[("/api/s2p/lead-time/summary", "lead_time_summary")].response_model is not None
    assert endpoints[("/api/s2p/lead-time/alerts", "lead_time_alerts")].response_model is not None
    assert endpoints[("/api/s2p/lead-time/suppliers", "lead_time_suppliers")].response_model is not None
    assert endpoints[("/api/s2p/lead-time/suppliers/{supplier_id}", "lead_time_supplier")].response_model is not None
