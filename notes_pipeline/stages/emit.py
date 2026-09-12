from __future__ import annotations

import shutil
import warnings
from datetime import datetime
from pathlib import Path

import yaml

from ..models import Lecture

_REQUIRED_HEADINGS = [
    "## TL;DR",
    "## Exam signals",
    "## Notes",
    "## Gaps in my notes",
    "## Glossary",
    "## Open questions",
    "### Transcription confidence",
]

_FRONTMATTER_KEY_ORDER = [
    "course", "lecture", "title", "instructor", "date",
    "source_audio", "source_deck", "duration",
    "transcript_engine", "generated", "model",
]


class MissingHeadingsWarning(UserWarning):
    """Issued when generated notes are missing a required §6 section. A
    partial note file still beats none, so this warns rather than fails."""


def emit(markdown: str, lecture: Lecture, meta: dict, dst: Path) -> Path:
    """Prepend YAML frontmatter to the synthesized markdown and write it to
    dst, archiving any existing file at dst first so regeneration never
    silently destroys a previous version."""
    _check_headings(markdown)

    frontmatter = _render_frontmatter(_build_frontmatter(lecture, meta))
    content = frontmatter + "\n" + markdown.rstrip() + "\n"

    if dst.exists():
        _archive_existing(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")
    return dst


def _check_headings(markdown: str) -> None:
    missing = [h for h in _REQUIRED_HEADINGS if h not in markdown]
    if missing:
        warnings.warn(
            "Generated notes are missing required section(s): " + ", ".join(missing),
            MissingHeadingsWarning,
            stacklevel=2,
        )


def _build_frontmatter(lecture: Lecture, meta: dict) -> dict:
    values = dict(meta)
    values.setdefault("course", lecture.course)
    values["lecture"] = lecture.number
    if lecture.title:
        values.setdefault("title", lecture.title)
    if lecture.date:
        values.setdefault("date", lecture.date.isoformat())

    ordered = {key: values.pop(key) for key in _FRONTMATTER_KEY_ORDER if key in values}
    ordered.update(values)  # any extra caller-supplied fields, appended at the end
    return ordered


def _render_frontmatter(fields: dict) -> str:
    body = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{body}---\n"


def _archive_existing(dst: Path) -> None:
    history_dir = dst.parent / ".history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    archived_path = history_dir / f"{dst.stem}-{timestamp}{dst.suffix}"
    counter = 1
    while archived_path.exists():
        archived_path = history_dir / f"{dst.stem}-{timestamp}-{counter}{dst.suffix}"
        counter += 1
    shutil.move(str(dst), str(archived_path))
