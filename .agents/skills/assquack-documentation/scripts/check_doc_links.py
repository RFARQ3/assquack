#!/usr/bin/env python3
"""Validate local Markdown links in README.md and docs/**/*.md."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote


LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
PUNCTUATION_PATTERN = re.compile(r"[^\w\s-]")
UNDERSCORE_PATTERN = re.compile(r"_")
WHITESPACE_PATTERN = re.compile(r"\s+")
HYPHEN_PATTERN = re.compile(r"-+")


def iter_markdown_files(root: Path) -> list[Path]:
    """Return the small documentation surface owned by the docs skill."""

    docs = sorted((root / "docs").rglob("*.md"))
    readme = root / "README.md"

    return [readme, *docs]


def is_external_target(target: str) -> bool:
    return "://" in target or target.startswith("mailto:")


def split_target(target: str) -> tuple[str, str]:
    file_target, _, fragment = target.partition("#")
    return file_target, unquote(fragment)


def normalize_link_target(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]

    return target


def strip_fenced_blocks(text: str) -> str:
    return FENCE_PATTERN.sub("", text)


def slug_heading(heading: str) -> str:
    heading = heading.strip().lower()
    heading = PUNCTUATION_PATTERN.sub("", heading)
    heading = UNDERSCORE_PATTERN.sub("", heading)
    heading = WHITESPACE_PATTERN.sub("-", heading)
    heading = HYPHEN_PATTERN.sub("-", heading)

    return heading.strip("-")


def collect_heading_anchors(path: Path) -> set[str]:
    text = strip_fenced_blocks(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    anchors: set[str] = set()

    for match in HEADING_PATTERN.finditer(text):
        slug = slug_heading(match.group(2))
        if not slug:
            continue

        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")

    return anchors


def main() -> int:
    root = Path.cwd()
    missing: list[str] = []
    anchors_by_path: dict[Path, set[str]] = {}

    for path in iter_markdown_files(root):
        text = strip_fenced_blocks(path.read_text(encoding="utf-8"))

        for match in LINK_PATTERN.finditer(text):
            raw_target = normalize_link_target(match.group(1))
            if not raw_target or is_external_target(raw_target):
                continue

            target, fragment = split_target(raw_target)
            if not target:
                target = path.name

            resolved = path.parent / target
            if not resolved.exists():
                missing.append(f"{path}:{match.start()}: {target}")
                continue

            if fragment and resolved.suffix == ".md":
                anchors = anchors_by_path.setdefault(
                    resolved, collect_heading_anchors(resolved)
                )
                if fragment not in anchors:
                    missing.append(f"{path}:{match.start()}: {target}#{fragment}")

    if missing:
        print("Missing local Markdown link targets or anchors:")
        print("\n".join(missing))
        return 1

    print("All local Markdown link targets and anchors exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
