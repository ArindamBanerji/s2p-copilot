from concurrent.futures import ThreadPoolExecutor

from app.routers.s2p import InvoiceLockManager


def test_different_invoices_can_be_held_in_parallel():
    manager = InvoiceLockManager()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(manager.acquire, "invoice-a", 0.2)
        second = executor.submit(manager.acquire, "invoice-b", 0.2)
        assert first.result() is True
        assert second.result() is True
    manager.release("invoice-a")
    manager.release("invoice-b")


def test_same_invoice_waits_then_acquires_after_release():
    manager = InvoiceLockManager()
    assert manager.acquire("invoice-a", 0.2) is True
    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(manager.acquire, "invoice-a", 0.5)
        manager.release("invoice-a")
        assert waiting.result() is True
    manager.release("invoice-a")


def test_same_invoice_timeout_does_not_leak_entry():
    manager = InvoiceLockManager()
    assert manager.acquire("invoice-a", 0.2) is True
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(manager.acquire, "invoice-a", 0.01).result() is False
    assert manager.size() == 1
    manager.release("invoice-a")
    assert manager.size() == 0


def test_lock_entries_are_cleaned_after_release():
    manager = InvoiceLockManager()
    assert manager.acquire("invoice-a", 0.2) is True
    assert manager.size() == 1
    manager.release("invoice-a")
    assert manager.size() == 0
