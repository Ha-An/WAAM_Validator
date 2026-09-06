from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _documentation_files() -> list[Path]:
    return [
        Path("README.md"),
        *sorted(Path("docs").rglob("*.md")),
        *sorted(path for path in Path("tests").glob("*.md")),
    ]


@pytest.mark.parametrize("document", _documentation_files(), ids=str)
def test_local_markdown_links_resolve(document: Path) -> None:
    missing: list[str] = []
    for raw_target in _MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
        target = raw_target.strip().strip("<>")
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        relative = unquote(target.split("#", 1)[0])
        if relative and not (document.parent / relative).resolve().exists():
            missing.append(raw_target)
    assert not missing, f"깨진 로컬 Markdown 링크: {missing}"
