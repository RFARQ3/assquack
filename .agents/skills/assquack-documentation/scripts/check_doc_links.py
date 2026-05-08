#!/usr/bin/env python3
"""Validate local Markdown links in README.md and docs/*.md."""

from __future__ import annotations

import re
import sys
from pathlib import Path


LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def iter_markdown_files(root: Path) -> list[Path]:
    """Return the small documentation surface owned by the docs skill."""

    docs = sorted((root / "docs").glob("*.md"))
    readme = root / "README.md"

    return [readme, *docs]


def is_external_target(target: str) -> bool:
    return "://" in target or target.startswith(("mailto:", "#"))


def strip_fragment(target: str) -> str:
    return target.split("#", 1)[0]


def main() -> int:
    root = Path.cwd()
    missing: list[str] = []

    for path in iter_markdown_files(root):
        text = path.read_text(encoding="utf-8")

        for match in LINK_PATTERN.finditer(text):
            target = strip_fragment(match.group(1))
            if not target or is_external_target(target):
                continue

            resolved = path.parent / target
            if not resolved.exists():
                missing.append(f"{path}:{match.start()}: {target}")

    if missing:
        print("Missing local Markdown link targets:")
        print("\n".join(missing))
        return 1

    print("All local Markdown link targets exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
