"""K4 supplier intelligence provider for S2P cold-start enrichment."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from app.connectors.fda_client import FDAClient
from app.connectors.sec_client import SECClient


T = TypeVar("T")


@dataclass(frozen=True)
class Provenanced(Generic[T]):
    """Value wrapper carrying source and substantiation tier."""

    value: T
    source: str
    provenance_tier: str
    fetched_at: float | None = None


class SupplierIntelProvider:
    """K4 supplier intelligence with provenance-tagged cascade.

    Sources:
      SEC EDGAR: public company filings (10-K, 10-Q)
      FDA: food/drug enforcement actions and recalls

    Cascade: live -> cached -> fixture("sample").
    """

    provenance_tier = "scraped_external"

    def __init__(
        self,
        *,
        sec_client: SECClient | None = None,
        fda_client: FDAClient | None = None,
        cache_dir: Path | None = None,
        cache_ttl_hours: int = 24,
    ):
        self._cache_dir = Path(cache_dir) if cache_dir else self._default_cache_dir()
        self._cache_ttl_seconds = cache_ttl_hours * 60 * 60
        self._sec = sec_client or SECClient(cache_dir=self._cache_dir)
        self._fda = fda_client or FDAClient(cache_dir=self._cache_dir)

    def company_filings(self, company_name: str) -> Provenanced[list[dict]]:
        """Recent SEC filings for a supplier."""

        return self._cascade(
            cache_key=f"sec_filings_{self._slug(company_name)}",
            live=lambda: self._sec.search_filings(company_name, form_type="10-K", limit=5),
            fixture=lambda: self._fixture_filings(company_name),
        )

    def safety_recalls(
        self, *, keyword: str | None = None, days: int = 90
    ) -> Provenanced[list[dict]]:
        """FDA enforcement actions and recalls."""

        return self._cascade(
            cache_key=f"fda_recalls_{days}_{self._slug(keyword or 'all')}",
            live=lambda: self._fda.recent_recalls(keyword=keyword, limit=25),
            fixture=lambda: self._fixture_recalls(keyword),
        )

    def supplier_risk_profile(self, supplier: dict) -> Provenanced[dict]:
        """Combined supplier risk profile from public sources."""

        supplier_name = str(
            supplier.get("supplier_name") or supplier.get("name") or supplier.get("supplier_id") or "unknown"
        )
        return self._cascade(
            cache_key=f"risk_profile_{self._slug(supplier_name)}",
            live=lambda: self._build_live_risk_profile(supplier_name),
            fixture=lambda: self._fixture_risk_profile(supplier_name),
        )

    def _build_live_risk_profile(self, supplier_name: str) -> dict:
        filings = self._sec.search_filings(supplier_name, form_type="10-K", limit=5)
        recalls = self._fda.recall_by_company(supplier_name)
        return self._compose_profile(
            supplier_name=supplier_name,
            filings=filings,
            recalls=recalls,
            source="live",
            provenance_tier=self.provenance_tier,
        )

    @staticmethod
    def _compose_profile(
        *,
        supplier_name: str,
        filings: list[dict],
        recalls: list[dict],
        source: str,
        provenance_tier: str,
    ) -> dict:
        recall_penalty = min(0.5, 0.12 * len(recalls))
        filing_credit = 0.15 if filings else 0.0
        risk_score = max(0.0, min(1.0, 0.35 + recall_penalty - filing_credit))
        return {
            "supplier_name": supplier_name,
            "risk_score": round(risk_score, 3),
            "filing_count": len(filings),
            "recall_count": len(recalls),
            "filings": filings,
            "recalls": recalls,
            "source": source,
            "provenance_tier": provenance_tier,
        }

    def _cascade(
        self,
        *,
        cache_key: str,
        live: Callable[[], T],
        fixture: Callable[[], T],
    ) -> Provenanced[T]:
        try:
            value = live()
            if self._is_empty(value):
                return Provenanced(
                    value=value,
                    source="live",
                    provenance_tier=self.provenance_tier,
                    fetched_at=time.time(),
                )
            self._write_cache(cache_key, value)
            return Provenanced(
                value=value,
                source="live",
                provenance_tier=self.provenance_tier,
                fetched_at=time.time(),
            )
        except Exception:
            cached = self._read_cache(cache_key)
            if cached is not None:
                return Provenanced(
                    value=cached,
                    source="cached",
                    provenance_tier=self.provenance_tier,
                    fetched_at=time.time(),
                )
            return Provenanced(
                value=fixture(),
                source="sample",
                provenance_tier="sample",
                fetched_at=time.time(),
            )

    def _write_cache(self, key: str, value: Any) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"fetched_at": time.time(), "value": value}, indent=2),
            encoding="utf-8",
        )

    def _read_cache(self, key: str) -> Any | None:
        path = self._cache_path(key)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at", 0))
        if time.time() - fetched_at > self._cache_ttl_seconds:
            return None
        return payload.get("value")

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    @staticmethod
    def _is_empty(value: Any) -> bool:
        return value == [] or value == {}

    @staticmethod
    def _default_cache_dir() -> Path:
        return Path(__file__).resolve().parents[2] / "data" / "supplier_intel_cache"

    @staticmethod
    def _fixture_filings(company_name: str) -> list[dict]:
        return [
            {
                "company": company_name,
                "form_type": "10-K",
                "filing_date": "2025-01-01",
                "title": "Sample annual report for offline S2P demo mode",
                "provenance": "sample",
                "provenance_tier": "sample",
            }
        ]

    @staticmethod
    def _fixture_recalls(keyword: str | None) -> list[dict]:
        return [
            {
                "recall_number": "F-0000-2025",
                "company": "Sample Supplier",
                "product": keyword or "Sample product",
                "reason": "Sample recall for offline S2P demo mode",
                "classification": "Class II",
                "provenance": "sample",
                "provenance_tier": "sample",
            }
        ]

    @classmethod
    def _fixture_risk_profile(cls, supplier_name: str) -> dict:
        return cls._compose_profile(
            supplier_name=supplier_name,
            filings=cls._fixture_filings(supplier_name),
            recalls=cls._fixture_recalls(supplier_name),
            source="sample",
            provenance_tier="sample",
        )

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_") or "all"


class MockSupplierIntelProvider:
    """Fixture-backed supplier intelligence provider for tests and offline demos."""

    provenance_tier = "sample"

    def company_filings(self, company_name: str) -> Provenanced[list[dict]]:
        return Provenanced(
            value=SupplierIntelProvider._fixture_filings(company_name),
            source="sample",
            provenance_tier="sample",
            fetched_at=time.time(),
        )

    def safety_recalls(
        self, *, keyword: str | None = None, days: int = 90
    ) -> Provenanced[list[dict]]:
        return Provenanced(
            value=SupplierIntelProvider._fixture_recalls(keyword),
            source="sample",
            provenance_tier="sample",
            fetched_at=time.time(),
        )

    def supplier_risk_profile(self, supplier: dict) -> Provenanced[dict]:
        supplier_name = str(
            supplier.get("supplier_name") or supplier.get("name") or supplier.get("supplier_id") or "unknown"
        )
        return Provenanced(
            value=SupplierIntelProvider._fixture_risk_profile(supplier_name),
            source="sample",
            provenance_tier="sample",
            fetched_at=time.time(),
        )
