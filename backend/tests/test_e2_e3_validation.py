"""Focused tests for the E2-S2P and E3-S2P validation scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.scan_forbidden_patterns import build_report, scan_tree
from scripts.verify_s2p_domain_isolation import build_report as build_isolation_report


class FakeStore:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def get_all_decisions(self, domain: str | None = None) -> list[dict[str, object]]:
        return [row for row in self.rows if domain is None or row.get("domain") == domain]

    def get_decisions(self, domain: str, **_: object) -> list[dict[str, object]]:
        return self.get_all_decisions(domain)

    def get_verified_decisions(self, domain: str) -> list[dict[str, object]]:
        return self.get_all_decisions(domain)

    def get_decision_links(self, **_: object) -> list[dict[str, object]]:
        return [{"domain": "s2p"}]


class FakeReader:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def get_decisions(self) -> list[dict[str, object]]:
        return self.store.get_decisions("s2p")

    def get_all_decisions(self) -> list[dict[str, object]]:
        return self.store.get_all_decisions("s2p")

    def get_verified_decisions(self) -> list[dict[str, object]]:
        return self.store.get_verified_decisions("s2p")

    def get_decision_links(self) -> list[dict[str, object]]:
        return self.store.get_decision_links(domain="s2p")


def test_E2_01_isolation_clean_store() -> None:
    report = build_isolation_report(FakeStore([{"domain": "s2p"}]), FakeReader)
    assert report["isolated"] is True
    assert report["counts"]["trading_count"] == 0


def test_E2_02_isolation_detects_cross_domain_data() -> None:
    report = build_isolation_report(
        FakeStore([{"domain": "s2p"}, {"domain": "trading"}]), FakeReader
    )
    assert report["isolated"] is False
    assert report["counts"]["trading_count"] == 1


def test_E2_03_report_is_json_serializable() -> None:
    report = build_isolation_report(FakeStore([]), FakeReader)
    json.dumps(report)


def test_E2_04_script_skips_without_graph_dsn() -> None:
    script = Path(__file__).parents[1] / "scripts" / "verify_s2p_domain_isolation.py"
    env = dict(os.environ)
    env.pop("GRAPH_DSN", None)
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env)
    assert result.returncode == 0
    assert json.loads(result.stdout)["skipped"] is True


def test_E3_01_current_app_is_clean() -> None:
    app_root = Path(__file__).parents[1] / "app"
    assert build_report(app_root)["clean"] is True


def test_E3_02_scanner_detects_neo4j_import(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("import neo4j\n", encoding="utf-8")
    assert any(item.pattern == "neo4j-import" for item in scan_tree(tmp_path))


def test_E3_03_scanner_detects_bare_except(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("try:\n    pass\nexcept:\n    pass\n", encoding="utf-8")
    assert any(item.pattern == "bare-except" for item in scan_tree(tmp_path))


def test_E3_04_report_is_valid_json(tmp_path: Path) -> None:
    (tmp_path / "clean.py").write_text("value = 1\n", encoding="utf-8")
    json.dumps(build_report(tmp_path))
