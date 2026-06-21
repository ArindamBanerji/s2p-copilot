"""openFDA client for supplier safety recall intelligence."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast


class FDAClient:
    """FDA openFDA enforcement/recall API.

    Public, no API key for basic access. Rate-limited.
    """

    BASE_URL = "https://api.fda.gov/food/enforcement.json"

    def __init__(self, *, cache_dir: Path | None = None):
        self._cache_dir = Path(cache_dir) if cache_dir else None

    def recent_recalls(self, *, keyword: str | None = None, limit: int = 25) -> list[dict]:
        """Recent food enforcement actions and recalls."""

        params = {"limit": str(limit), "sort": "report_date:desc"}
        if keyword:
            params["search"] = f'reason_for_recall:"{keyword}"'
        return self.parse_response(self._fetch_json(params), limit=limit)

    def recall_by_company(self, company_name: str) -> list[dict]:
        """Recall records associated with a supplier name."""

        params = {
            "limit": "25",
            "sort": "report_date:desc",
            "search": f'recalling_firm:"{company_name}"',
        }
        return self.parse_response(self._fetch_json(params), limit=25)

    def _fetch_json(self, params: dict[str, str]) -> dict:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(f"{self.BASE_URL}?{query}")
        request.add_header("Accept", "application/json")
        with urllib.request.urlopen(request, timeout=30) as response:
            return cast(dict[Any, Any], json.loads(response.read().decode("utf-8")))

    @classmethod
    def parse_response(cls, payload: dict[str, Any], *, limit: int = 25) -> list[dict]:
        """Parse openFDA enforcement responses into compact recall records."""

        parsed = []
        for item in payload.get("results", []):
            parsed.append(
                {
                    "recall_number": item.get("recall_number"),
                    "company": item.get("recalling_firm"),
                    "product": item.get("product_description"),
                    "reason": item.get("reason_for_recall"),
                    "classification": item.get("classification"),
                    "status": item.get("status"),
                    "report_date": item.get("report_date"),
                    "state": item.get("state"),
                    "country": item.get("country"),
                    "provenance_tier": "scraped_external",
                }
            )
            if len(parsed) >= limit:
                break
        return parsed
