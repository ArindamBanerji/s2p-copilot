"""SEC EDGAR client for public supplier filing intelligence."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast


class SECClient:
    """SEC EDGAR full-text search API.

    Public, no API key. Rate limit: 10 req/sec.
    User-Agent header required per SEC policy.
    """

    BASE_URL = "https://efts.sec.gov/LATEST/search-index"

    def __init__(self, *, user_agent: str = "copilot-sdk/1.0", cache_dir: Path | None = None):
        if not user_agent:
            raise ValueError("SEC User-Agent header is required")
        self._user_agent = user_agent
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._last_request_at = 0.0

    def search_filings(
        self, company: str, *, form_type: str = "10-K", limit: int = 5
    ) -> list[dict]:
        """Search recent SEC filings for a company and form type."""

        params = {"q": company, "forms": form_type, "size": str(limit)}
        return self.parse_response(
            self._fetch_json(params),
            form_type=form_type,
            limit=limit,
        )

    def _fetch_json(self, params: dict[str, str]) -> dict:
        self._respect_rate_limit()
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{self.BASE_URL}?{query}")
        request.add_header("User-Agent", self._user_agent)
        request.add_header("Accept", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            return cast(dict[Any, Any], json.loads(response.read().decode("utf-8")))

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_at
        if elapsed < 0.1:
            time.sleep(0.1 - elapsed)
        self._last_request_at = time.time()

    @property
    def user_agent(self) -> str:
        return self._user_agent

    @classmethod
    def parse_response(
        cls, payload: dict[str, Any], *, form_type: str | None = None, limit: int = 5
    ) -> list[dict]:
        """Parse SEC search response hits into compact filing records."""

        hits = payload.get("hits", {}).get("hits", [])
        parsed = []
        for hit in hits:
            source = hit.get("_source", {})
            forms = source.get("form") or source.get("forms") or []
            if isinstance(forms, str):
                forms = [forms]
            normalized_forms = [str(form) for form in forms]
            if form_type and form_type not in normalized_forms:
                continue
            parsed.append(
                {
                    "company": source.get("display_names") or source.get("company"),
                    "cik": source.get("ciks", [None])[0] if isinstance(source.get("ciks"), list) else source.get("cik"),
                    "form_type": normalized_forms[0] if normalized_forms else form_type,
                    "filing_date": source.get("file_date") or source.get("filed_at"),
                    "accession_no": source.get("adsh") or source.get("accession_no"),
                    "title": source.get("title") or source.get("document_title"),
                    "provenance_tier": "scraped_external",
                }
            )
            if len(parsed) >= limit:
                break
        return parsed


class SECError(RuntimeError):
    """Raised when SEC client responses cannot be used."""
