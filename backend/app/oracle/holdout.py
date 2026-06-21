"""Conditional enrichment holdout for S2P."""

from __future__ import annotations

import hashlib


class ConditionalHoldout:
    """S2P-specific holdout: suppression conditional on enrichment.

    SOC = unconditional (every alert gets 15% holdout).
    S2P = conditional (only suppliers WITH enrichment get holdout).

    A new buyer has no enrichment for most suppliers, so holdout only applies
    to the enriched subset. This is the shape instance 2 reveals.
    """

    def __init__(self, holdout_pct: int = 15, seed: int = 42):
        self._pct = holdout_pct
        self._seed = seed

    def suppressed(self, supplier_id: str, has_enrichment: bool) -> bool:
        """Return deterministic per-supplier holdout, conditional on enrichment."""
        if not has_enrichment:
            return False

        digest = hashlib.sha256(f"{self._seed}:{self._pct}:{supplier_id}".encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % 100
        return bucket < self._pct
