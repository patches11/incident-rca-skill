from pathlib import Path

import pytest

from adapters.fixture import FixtureAdapter
from pipeline import Investigation

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def scenario_dir():
    d = REPO / "scenarios" / "pool-exhaustion"
    if not d.exists():
        pytest.skip("run `make fixtures` first")
    return d


@pytest.fixture
def inv(scenario_dir, tmp_path):
    adapter = FixtureAdapter(scenario_dir, REPO / "org-context", REPO / "org-context" / "overlay.md")
    return Investigation(adapter, tmp_path / "audit.jsonl")
