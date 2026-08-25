from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    root = Path(__file__).parent / "fixtures"
    assert (root / "collision_free" / "target.stl").is_file()
    return root
