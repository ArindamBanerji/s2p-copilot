from __future__ import annotations

import urllib.error

import pytest

from app.connectors.fda_client import FDAClient
from app.connectors.sec_client import SECClient
from app.connectors.supplier_intel_provider import (
    MockSupplierIntelProvider,
    SupplierIntelProvider,
)
from app.routers.s2p_data_helpers import assert_no_sample_in_metric, is_sample_data


FILINGS = [
    {
        "company": ["ACME Corp"],
        "cik": "0000001",
        "form_type": "10-K",
        "filing_date": "2025-02-01",
        "accession_no": "0000001-25-000001",
        "title": "Annual report",
        "provenance_tier": "scraped_external",
    }
]

RECALLS = [
    {
        "recall_number": "F-1234-2025",
        "company": "ACME Corp",
        "product": "Packaged food",
        "reason": "Undeclared allergen",
        "classification": "Class II",
        "status": "Ongoing",
        "report_date": "20250201",
        "provenance_tier": "scraped_external",
    }
]


class FakeSECClient:
    def __init__(self, *, error: Exception | None = None, empty: bool = False):
        self.error = error
        self.empty = empty

    def search_filings(
        self, company: str, *, form_type: str = "10-K", limit: int = 5
    ) -> list[dict]:
        if self.error:
            raise self.error
        if self.empty:
            return []
        return FILINGS[:limit]


class FakeFDAClient:
    def __init__(self, *, error: Exception | None = None, empty: bool = False):
        self.error = error
        self.empty = empty
        self.last_keyword = None
        self.last_company = None

    def recent_recalls(self, *, keyword: str | None = None, limit: int = 25) -> list[dict]:
        self.last_keyword = keyword
        if self.error:
            raise self.error
        if self.empty:
            return []
        return RECALLS[:limit]

    def recall_by_company(self, company_name: str) -> list[dict]:
        self.last_company = company_name
        if self.error:
            raise self.error
        if self.empty:
            return []
        return RECALLS


def _provider(tmp_path, *, sec=None, fda=None) -> SupplierIntelProvider:
    return SupplierIntelProvider(
        sec_client=sec or FakeSECClient(),
        fda_client=fda or FakeFDAClient(),
        cache_dir=tmp_path,
    )


def test_provider_provenance_tier():
    assert SupplierIntelProvider.provenance_tier == "scraped_external"


def test_mock_provenance_tier():
    assert MockSupplierIntelProvider.provenance_tier == "sample"


def test_company_filings_returns_list(tmp_path):
    result = _provider(tmp_path).company_filings("ACME Corp")

    assert result.source == "live"
    assert result.value
    assert result.value[0]["form_type"] == "10-K"


def test_safety_recalls_returns_list(tmp_path):
    result = _provider(tmp_path).safety_recalls(keyword="allergen")

    assert result.source == "live"
    assert result.value
    assert result.value[0]["recall_number"] == "F-1234-2025"


def test_supplier_risk_profile_has_fields(tmp_path):
    result = _provider(tmp_path).supplier_risk_profile({"supplier_name": "ACME Corp"})

    assert {"risk_score", "source", "filing_count", "recall_count", "provenance_tier"} <= set(
        result.value
    )
    assert 0.0 <= result.value["risk_score"] <= 1.0


def test_risk_profile_provenance(tmp_path):
    result = _provider(tmp_path).supplier_risk_profile({"supplier_name": "ACME Corp"})

    assert result.provenance_tier == "scraped_external"
    assert result.value["provenance_tier"] == "scraped_external"


def test_cascade_live_to_cached(tmp_path):
    result = _provider(tmp_path).company_filings("ACME Corp")

    assert result.source == "live"
    assert (tmp_path / "sec_filings_acme_corp.json").is_file()


def test_cascade_cached_serves(tmp_path):
    _provider(tmp_path).company_filings("ACME Corp")
    provider = _provider(tmp_path, sec=FakeSECClient(error=ConnectionError("down")))

    result = provider.company_filings("ACME Corp")

    assert result.source == "cached"
    assert result.provenance_tier == "scraped_external"
    assert result.value == FILINGS


def test_cascade_fixture_fallback(tmp_path):
    provider = _provider(tmp_path, sec=FakeSECClient(error=ConnectionError("down")))

    result = provider.company_filings("ACME Corp")

    assert result.source == "sample"
    assert result.provenance_tier == "sample"
    assert result.value[0]["provenance"] == "sample"


def test_cascade_never_unlabeled(tmp_path):
    provider = _provider(tmp_path)

    responses = [
        provider.company_filings("ACME Corp"),
        provider.safety_recalls(keyword="allergen"),
        provider.supplier_risk_profile({"supplier_name": "ACME Corp"}),
    ]

    assert all(response.source for response in responses)
    assert all(response.provenance_tier for response in responses)


def test_network_error_uses_cache(tmp_path):
    _provider(tmp_path).company_filings("ACME Corp")
    provider = _provider(tmp_path, sec=FakeSECClient(error=ConnectionError("network")))

    assert provider.company_filings("ACME Corp").source == "cached"


def test_timeout_uses_cache(tmp_path):
    _provider(tmp_path).company_filings("ACME Corp")
    provider = _provider(tmp_path, sec=FakeSECClient(error=TimeoutError("timeout")))

    assert provider.company_filings("ACME Corp").source == "cached"


def test_malformed_json_uses_cache(tmp_path):
    _provider(tmp_path).company_filings("ACME Corp")
    provider = _provider(tmp_path, sec=FakeSECClient(error=ValueError("bad json")))

    assert provider.company_filings("ACME Corp").source == "cached"


def test_rate_limit_backoff(tmp_path):
    _provider(tmp_path).company_filings("ACME Corp")
    error = urllib.error.HTTPError(
        url="https://efts.sec.gov/LATEST/search-index",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=None,
    )
    provider = _provider(tmp_path, sec=FakeSECClient(error=error))

    assert provider.company_filings("ACME Corp").source == "cached"


def test_empty_response_not_cached(tmp_path):
    provider = _provider(tmp_path, sec=FakeSECClient(empty=True))

    result = provider.company_filings("ACME Corp")

    assert result.source == "live"
    assert result.value == []
    assert not (tmp_path / "sec_filings_acme_corp.json").exists()


def test_sec_parse_filing():
    payload = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "display_names": ["ACME Corp"],
                        "ciks": ["0000001"],
                        "form": ["10-K"],
                        "file_date": "2025-02-01",
                        "adsh": "0000001-25-000001",
                        "title": "Annual report",
                    }
                }
            ]
        }
    }

    parsed = SECClient.parse_response(payload, form_type="10-K")

    assert parsed[0]["company"] == ["ACME Corp"]
    assert parsed[0]["cik"] == "0000001"
    assert parsed[0]["provenance_tier"] == "scraped_external"


def test_sec_user_agent_required():
    assert SECClient(user_agent="s2p-copilot-test/1.0").user_agent == "s2p-copilot-test/1.0"
    with pytest.raises(ValueError):
        SECClient(user_agent="")


def test_sec_search_form_filter():
    payload = {
        "hits": {
            "hits": [
                {"_source": {"display_names": ["A"], "form": ["8-K"]}},
                {"_source": {"display_names": ["B"], "form": ["10-K"]}},
            ]
        }
    }

    parsed = SECClient.parse_response(payload, form_type="10-K")

    assert len(parsed) == 1
    assert parsed[0]["company"] == ["B"]


def test_fda_parse_recall():
    payload = {
        "results": [
            {
                "recall_number": "F-1234-2025",
                "recalling_firm": "ACME Corp",
                "product_description": "Packaged food",
                "reason_for_recall": "Undeclared allergen",
                "classification": "Class II",
                "status": "Ongoing",
                "report_date": "20250201",
                "state": "CA",
                "country": "United States",
            }
        ]
    }

    parsed = FDAClient.parse_response(payload)

    assert parsed[0]["company"] == "ACME Corp"
    assert parsed[0]["reason"] == "Undeclared allergen"
    assert parsed[0]["provenance_tier"] == "scraped_external"


def test_fda_keyword_filter(tmp_path):
    fda = FakeFDAClient()
    provider = _provider(tmp_path, fda=fda)

    provider.safety_recalls(keyword="allergen")

    assert fda.last_keyword == "allergen"


def test_fda_company_filter(tmp_path):
    fda = FakeFDAClient()
    provider = _provider(tmp_path, fda=fda)

    provider.supplier_risk_profile({"supplier_name": "ACME Corp"})

    assert fda.last_company == "ACME Corp"


def test_mock_not_labeled_live():
    result = MockSupplierIntelProvider().company_filings("ACME Corp")

    assert MockSupplierIntelProvider.provenance_tier != "scraped_external"
    assert result.provenance_tier == "sample"


def test_f26_gate_rejects_sample():
    sample = MockSupplierIntelProvider().company_filings("ACME Corp")

    with pytest.raises(ValueError, match="F-26 VIOLATION"):
        assert_no_sample_in_metric([sample], "supplier_risk")


def test_f26_gate_passes_real(tmp_path):
    real = _provider(tmp_path).company_filings("ACME Corp")

    assert_no_sample_in_metric([real], "supplier_risk")


def test_is_sample_data_with_provider(tmp_path):
    result = _provider(tmp_path).company_filings("ACME Corp")

    assert is_sample_data(result) is False


def test_is_sample_data_with_mock():
    result = MockSupplierIntelProvider().company_filings("ACME Corp")

    assert is_sample_data(result) is True
