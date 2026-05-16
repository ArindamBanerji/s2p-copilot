from __future__ import annotations

from pathlib import Path


MAIN_PY = Path(__file__).resolve().parents[1] / "app" / "main.py"
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:5176",
    "http://localhost:5177",
    "http://127.0.0.1:5177",
]


def _source() -> str:
    return MAIN_PY.read_text(encoding="utf-8")


def test_s2p_does_not_allow_wildcard_cors() -> None:
    source = _source()
    assert 'allow_origins=["*"]' not in source
    assert "allow_origins=['*']" not in source


def test_s2p_uses_cors_origins_env() -> None:
    source = _source()
    assert "CORS_ORIGINS" in source
    assert "os.environ.get" in source
    assert '.split(",")' in source
    assert "origin.strip()" in source
    assert "if origin.strip()" in source


def test_s2p_default_dev_origins_include_expected_ports() -> None:
    source = _source()
    for origin in DEV_ORIGINS:
        assert origin in source


def test_s2p_preserves_cors_flags() -> None:
    source = _source()
    assert "allow_credentials=False" in source
    assert 'allow_methods=["*"]' in source
    assert 'allow_headers=["*"]' in source
