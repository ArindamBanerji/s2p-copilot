from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from app.connectors.fda_client import FDAClient
from app.connectors.sec_client import SECClient
from app.connectors.supplier_intel_provider import (
    MockSupplierIntelProvider,
    SupplierIntelProvider,
)
from app.routers.s2p_data_helpers import assert_no_sample_in_metric


class FailingSECClient:
    def __init__(self, error: Exception):
        self._error = error

    def search_filings(
        self, company: str, *, form_type: str = "10-K", limit: int = 5
    ) -> list[dict]:
        raise self._error


class FailingFDAClient:
    def __init__(self, error: Exception):
        self._error = error

    def recent_recalls(self, *, keyword: str | None = None, limit: int = 25) -> list[dict]:
        raise self._error

    def recall_by_company(self, company_name: str) -> list[dict]:
        raise self._error


def test_provider_live_provenance():
    assert SupplierIntelProvider.provenance_tier == "scraped_external"


def test_mock_provenance():
    assert MockSupplierIntelProvider.provenance_tier == "sample"


def test_cascade_network_error(tmp_path):
    provider = SupplierIntelProvider(
        sec_client=FailingSECClient(ConnectionError("network down")),
        fda_client=FailingFDAClient(ConnectionError("network down")),
        cache_dir=tmp_path,
    )

    result = provider.company_filings("ACME Corp")

    assert result.source in {"cached", "sample"}
    assert result.provenance_tier in {"scraped_external", "sample"}


def test_cascade_timeout(tmp_path):
    provider = SupplierIntelProvider(
        sec_client=FailingSECClient(TimeoutError("timeout")),
        fda_client=FailingFDAClient(TimeoutError("timeout")),
        cache_dir=tmp_path,
    )

    result = provider.safety_recalls(keyword="allergen")

    assert result.source in {"cached", "sample"}
    assert result.provenance_tier in {"scraped_external", "sample"}


def test_sec_filing_parse():
    parsed = SECClient.parse_response(
        {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "display_names": ["ACME Corp"],
                            "ciks": ["0000001"],
                            "form": ["10-K"],
                            "file_date": "2025-02-01",
                            "adsh": "0000001-25-000001",
                        }
                    }
                ]
            }
        },
        form_type="10-K",
    )

    assert parsed[0]["company"] == ["ACME Corp"]
    assert parsed[0]["form_type"] == "10-K"
    assert parsed[0]["provenance_tier"] == "scraped_external"


def test_fda_recall_parse():
    parsed = FDAClient.parse_response(
        {
            "results": [
                {
                    "recall_number": "F-1234-2025",
                    "recalling_firm": "ACME Corp",
                    "product_description": "Packaged food",
                    "reason_for_recall": "Undeclared allergen",
                    "classification": "Class II",
                    "status": "Ongoing",
                    "report_date": "20250201",
                }
            ]
        }
    )

    assert parsed[0]["company"] == "ACME Corp"
    assert parsed[0]["reason"] == "Undeclared allergen"
    assert parsed[0]["provenance_tier"] == "scraped_external"


def test_sec_user_agent_set(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"hits": {"hits": []}}'

    def fake_urlopen(request: urllib.request.Request, timeout: int):
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    SECClient(user_agent="s2p-test/1.0")._fetch_json({"q": "ACME"})

    assert captured["user_agent"] == "s2p-test/1.0"
    assert captured["timeout"] == 30


def test_f25_mock_not_live():
    assert MockSupplierIntelProvider.provenance_tier != "scraped_external"


def test_provenance_labels_on_fixtures():
    data_dir = Path(__file__).resolve().parents[2] / "data"
    invoices = json.loads((data_dir / "synthetic_invoices.json").read_text(encoding="utf-8"))
    suppliers = json.loads((data_dir / "s2p_demo_suppliers.json").read_text(encoding="utf-8"))

    assert all(invoice.get("provenance") == "sample" for invoice in invoices)
    assert all(supplier.get("provenance") == "sample" for supplier in suppliers)


def test_f26_gate_with_sample_data():
    records = [{"supplier_id": "SUP-001", "provenance": "sample"}]

    try:
        assert_no_sample_in_metric(records, "supplier_risk")
    except ValueError as exc:
        assert "F-26 VIOLATION" in str(exc)
    else:
        raise AssertionError("sample records must be rejected")


def test_f26_gate_passes_real():
    records = [{"supplier_id": "SUP-001", "provenance": "scraped_external"}]

    assert_no_sample_in_metric(records, "supplier_risk")
