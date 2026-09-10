from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from pydantic import BaseModel

_STOPWORDS = frozenset(
    """
    a an the this that these those what when where why how who which
    there here then than and or but if so because as for nor yet
    in on at by to of with from into onto about above below between
    during after before over under again further once
    is are was were be been being have has had do does did
    will would should could can may might must shall
    it its we you they he she i our your their his her
    not no yes all any each few more most other some such only own same
    just also
    """.split()
)

_PHRASE_RE = re.compile(r"[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*")


def _format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


class Segment(BaseModel):
    start: float
    end: float
    text: str
    confidence: float | None = None


class Transcript(BaseModel):
    segments: list[Segment]
    duration: float
    engine: str
    language: str

    def to_timestamped_text(self) -> str:
        return "\n".join(f"[{_format_timestamp(seg.start)}] {seg.text}" for seg in self.segments)


class Slide(BaseModel):
    index: int
    title: str | None
    body: str
    notes: str | None


def _slide_text_lines(slide: Slide) -> Iterator[str]:
    if slide.title:
        yield slide.title
    yield from slide.body.splitlines()
    if slide.notes:
        yield from slide.notes.splitlines()


class Deck(BaseModel):
    slides: list[Slide]
    source: Path

    def vocabulary(self, limit: int = 120) -> list[str]:
        counts: Counter[str] = Counter()
        for slide in self.slides:
            for line in _slide_text_lines(slide):
                for match in _PHRASE_RE.finditer(line):
                    words = match.group().split()
                    while words and words[0].lower() in _STOPWORDS:
                        words.pop(0)
                    while words and words[-1].lower() in _STOPWORDS:
                        words.pop()
                    if not words:
                        continue
                    counts[" ".join(words)] += 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [term for term, _ in ranked[:limit]]


class LectureInputs(BaseModel):
    audio: Path
    deck: Path | None
    my_notes: Path | None
    extras: list[Path]


class Lecture(BaseModel):
    id: str
    course: str
    number: int
    title: str | None
    date: date | None
    dir: Path
