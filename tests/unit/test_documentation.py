from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import unquote

import pytest

_MARKDOWN_REFERENCE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _documentation_files() -> list[Path]:
    return [
        Path("README.md"),
        *sorted(Path("docs").rglob("*.md")),
        *sorted(path for path in Path("tests").glob("*.md")),
    ]


@pytest.mark.parametrize("document", _documentation_files(), ids=str)
def test_local_markdown_links_resolve(document: Path) -> None:
    missing: list[str] = []
    for raw_target in _MARKDOWN_REFERENCE.findall(document.read_text(encoding="utf-8")):
        target = raw_target.strip().strip("<>")
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative = unquote(target.split("#", 1)[0])
        if relative and not (document.parent / relative).resolve().exists():
            missing.append(raw_target)
    assert not missing, f"깨진 로컬 Markdown 링크: {missing}"


def test_apache_license_is_declared_consistently() -> None:
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]
    license_text = Path("LICENSE").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    notice = Path("NOTICE").read_text(encoding="utf-8")

    assert metadata["license"] == "Apache-2.0"
    assert metadata["license-files"] == ["LICENSE", "NOTICE"]
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "[Apache License 2.0](LICENSE)" in readme
    assert "Copyright 2026 Yosep Oh" in notice
    assert "AIIS Lab, Hanyang University ERICA" in notice
