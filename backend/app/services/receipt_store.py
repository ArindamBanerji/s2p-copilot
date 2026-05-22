"""In-memory outcome receipt store for S2P evidence chains."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from app.models.outcome_receipt import OutcomeReceipt


class ReceiptStore:
    def __init__(self, max_receipts: int = 10000):
        self.max_receipts = int(max_receipts)
        self._receipts: deque[OutcomeReceipt] = deque(maxlen=self.max_receipts)
        self._by_invoice: dict[str, list[OutcomeReceipt]] = defaultdict(list)

    @property
    def last_hash(self) -> str:
        return self._receipts[-1].receipt_hash if self._receipts else ""

    @property
    def count(self) -> int:
        return len(self._receipts)

    def add(self, receipt: OutcomeReceipt) -> OutcomeReceipt:
        if not receipt.previous_receipt_hash:
            receipt.previous_receipt_hash = self.last_hash
            receipt.receipt_hash = receipt.compute_hash()
        if len(self._receipts) == self.max_receipts and self._receipts:
            evicted = self._receipts[0]
            invoice_receipts = self._by_invoice.get(evicted.invoice_id, [])
            self._by_invoice[evicted.invoice_id] = [
                item for item in invoice_receipts if item.receipt_id != evicted.receipt_id
            ]
        self._receipts.append(receipt)
        self._by_invoice[receipt.invoice_id].append(receipt)
        return receipt

    def get_chain(self, limit: int = 100) -> list[dict[str, Any]]:
        limit_value = max(int(limit), 0)
        if limit_value == 0:
            return []
        return [receipt.to_dict() for receipt in list(self._receipts)[-limit_value:]]

    def get_for_invoice(self, invoice_id: str) -> list[dict[str, Any]]:
        return [receipt.to_dict() for receipt in self._by_invoice.get(invoice_id, [])]

    def verify_chain(self) -> dict[str, Any]:
        expected_previous = ""
        broken_at: int | None = None
        for index, receipt in enumerate(self._receipts):
            if receipt.previous_receipt_hash != expected_previous or receipt.compute_hash() != receipt.receipt_hash:
                broken_at = index
                break
            expected_previous = receipt.receipt_hash
        return {
            "verified": broken_at is None,
            "chain_length": len(self._receipts),
            "last_hash": self.last_hash,
            "broken_at_index": broken_at,
        }

    @property
    def stats(self) -> dict[str, Any]:
        total = len(self._receipts)
        overrides = sum(1 for receipt in self._receipts if receipt.human_action != receipt.scored_action)
        confirms = total - overrides
        return {
            "total_receipts": total,
            "confirms": confirms,
            "overrides": overrides,
            "override_rate": round(overrides / total, 6) if total else 0.0,
            "chain_valid": self.verify_chain()["verified"],
        }

    def clear(self) -> None:
        self._receipts.clear()
        self._by_invoice.clear()


_receipt_store = ReceiptStore()


def get_receipt_store() -> ReceiptStore:
    return _receipt_store


def reset_receipt_store() -> ReceiptStore:
    global _receipt_store
    _receipt_store = ReceiptStore()
    return _receipt_store
