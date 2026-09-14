from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Optional


class CourseNotFoundError(RuntimeError):
    pass


def load_course_toml(course_root: Path) -> dict:
    with open(course_root / "course.toml", "rb") as f:
        return tomllib.load(f)


def list_courses(library_root: Path) -> list[tuple[Path, dict]]:
    """Every course.toml under the library, as (course_root, info)."""
    return [
        (toml_path.parent, load_course_toml(toml_path.parent))
        for toml_path in sorted(library_root.glob("*/*/course.toml"))
    ]


def find_course(library_root: Path, code: str) -> tuple[Path, dict]:
    for course_root, info in list_courses(library_root):
        if info.get("code", "").lower() == code.lower():
            return course_root, info
    raise CourseNotFoundError(
        f"No course.toml with code {code!r} found under {library_root}. "
        f"Expected a layout like {library_root}/<term>/<course>/course.toml."
    )


def find_by_stem(directory: Path, stem: str) -> Optional[Path]:
    if not directory.is_dir():
        return None
    matches = sorted(p for p in directory.glob(f"{stem}.*") if p.is_file())
    return matches[0] if matches else None


def resolve_course_paths(course_root: Path, pattern: str, number: int) -> tuple[Path, Optional[Path], Optional[Path], Path]:
    stem = pattern.format(n=number)
    audio = find_by_stem(course_root / "audio", stem)
    if audio is None:
        raise CourseNotFoundError(f"No audio file matching {stem!r} found under {course_root / 'audio'}.")
    deck = find_by_stem(course_root / "slides", stem)
    notes = find_by_stem(course_root, stem)
    out = course_root / f"{stem}-lecture-notes.md"
    return audio, deck, notes, out


def guess_number(stem: str) -> int:
    match = re.search(r"\d+", stem)
    return int(match.group()) if match else 0


def _pattern_regex(pattern: str) -> re.Pattern:
    """Turn a course.toml `pattern` like "Week{n}" into a regex that
    extracts the lecture number from a matching filename stem."""
    escaped = re.escape(pattern).replace(re.escape("{n}"), r"(\d+)")
    return re.compile(f"^{escaped}$")

def list_lecture_numbers(course_root: Path, pattern: str) -> list[int]:
    """Lecture numbers discoverable from audio filenames matching `pattern`,
    regardless of whether they have been built yet."""
    audio_dir = course_root / "audio"
    if not audio_dir.is_dir():
        return []
    regex = _pattern_regex(pattern)
    numbers: set[int] = set()
    for path in audio_dir.iterdir():
        if not path.is_file():
            continue
        match = regex.match(path.stem)
        if match:
            numbers.add(int(match.group(1)))
    return sorted(numbers)
