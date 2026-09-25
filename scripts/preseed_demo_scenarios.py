"""Load and verify the planted S2P demo fixtures.

This is deliberately file-backed: the existing compliance and demo endpoints
are read-only, so no source-of-truth database mutation is performed here.
The loader is idempotent because fixture identity is checked before the
in-memory registry is populated, and it can optionally verify a live backend.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "demo_fixtures"
FIXTURE_NAMES = (
    "demo_container_context.json",
    "demo_extinction_history.json",
    "demo_kept_manual.json",
)


def _load(name: str) -> Any:
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _fixture_ids(name: str, payload: Any) -> list[str]:
    if name == "demo_container_context.json":
        return [str(payload["invoice_id"])]
    if name == "demo_extinction_history.json":
        return [str(item["className"]) for item in payload]
    return [str(payload["className"])]


def _validate(name: str, payload: Any) -> None:
    if name == "demo_extinction_history.json":
        if not isinstance(payload, list) or not payload:
            raise ValueError("extinction history must be a non-empty array")
        records = payload
    else:
        if not isinstance(payload, dict):
            raise ValueError(f"{name} must contain an object")
        records = [payload]
    if any(record.get("planted") is not True for record in records):
        raise ValueError(f"{name} contains an untagged record")


def _get_json(url: str) -> tuple[bool, str]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            json.loads(response.read())
        return True, "ok"
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return False, type(exc).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8002",
        help="optional running S2P backend URL used for renderability checks",
    )
    args = parser.parse_args()

    registry: dict[str, dict[str, Any]] = {}
    for name in FIXTURE_NAMES:
        payload = _load(name)
        _validate(name, payload)
        ids = _fixture_ids(name, payload)
        for fixture_id in ids:
            registry.setdefault(fixture_id, {"fixture": name, "planted": True})
        print(f"{name}: loaded={len(ids)} idempotent_registry_entries={len(registry)}")

    checks = {
        "compliance": f"{args.base_url.rstrip('/')}/api/s2p/evidence/compliance",
        "frozen_twin": f"{args.base_url.rstrip('/')}/api/s2p/learning/frozen-twin",
    }
    for label, url in checks.items():
        available, detail = _get_json(url)
        print(f"{label}: {'verified' if available else 'unavailable'} ({detail})")

    print("preseed: OK (file-backed, planted=true, repeat-safe; no source-of-truth mutation)")
    print("frozen-twin init: POST /api/s2p/learning/twin/freeze when a live demo needs initialization")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
