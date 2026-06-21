"""Shared pytest fixtures."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def play_store_page1(fixtures_dir: Path) -> list[dict]:
    raw = json.loads((fixtures_dir / "play_store_page1.json").read_text(encoding="utf-8"))
    for item in raw:
        item["at"] = datetime.fromisoformat(item["at"])
    return raw


@pytest.fixture
def patch_cache_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Redirect cache writes to a temp directory."""
    import pulse.ingestion.cache as cache_module

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(cache_module, "CACHE_ROOT", cache_root)
    return cache_root
