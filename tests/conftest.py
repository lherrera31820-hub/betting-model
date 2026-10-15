"""Repo-root import path for Prediction Core tests (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SLATE = ROOT / "tests" / "fixtures" / "slate.json"
SAMPLE_CANDIDATES = ROOT / "prediction_core" / "fixtures" / "candidates.json"


@pytest.fixture
def repo_root() -> Path:
    return ROOT


@pytest.fixture
def fixture_slate_path() -> Path:
    return FIXTURE_SLATE
