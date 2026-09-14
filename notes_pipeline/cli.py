from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

import typer
import yaml

from . import library
from .cache import Cache, cache_key
from .config import Config, load_config
from .models import Deck, Lecture, Transcript
from .pipeline import read_assets, read_text_file
from .pipeline import run_build as _pipeline_run_build
from .stages.audio import prepare as audio_prepare
from .stages.audio import wav_duration
from .stages.emit import emit as emit_notes
from .stages.slides import extract as extract_slides
from .stages.synthesize import synthesize_append
from .stages.transcribe import get_transcriber
from .store import Store

app = typer.Typer(help="Lecture notes pipeline: audio + slides + notes -> markdown.")

_STAGE_VERSION = "v1"


@app.callback()
def main() -> None:
    """Lecture notes pipeline."""


# -- course.toml resolution ---------------------------------------------------

CourseNotFoundError = library.CourseNotFoundError
_find_course = library.find_course
_resolve_course_paths = library.resolve_course_paths
_guess_number = library.guess_number


def _format_duration(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _parse_duration(text: str) -> float:
    minutes, _, secs = text.partition(":")
    return int(minutes) * 60 + float(secs)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.index("\n---\n", 4)
    frontmatter = yaml.safe_load(text[4:end]) or {}
    body = text[end + 5 :].strip()
    return frontmatter, body


# -- pipeline -------------------------------------------------------------

_IS_TTY = sys.stdout.isatty()
_LIVE_UPDATE_INTERVAL = 0.3  # seconds, throttles high-frequency progress callbacks


def _live_line(name: str, text: str) -> None:
    """Overwrite the current line in place on a real terminal; on redirected
    output (a log file, a pipe) just print a plain line instead, since \\r
    tricks only make sense for something a human is watching live."""
    if _IS_TTY:
        typer.echo(f"\r\033[K{name:<12}{text}", nl=False)
    else:
        typer.echo(f"{name:<12}{text}")


def _final_line(name: str, text: str) -> None:
    if _IS_TTY:
        typer.echo(f"\r\033[K{name:<12}{text}")
    else:
        typer.echo(f"{name:<12}{text}")


def _print_stage(name: str, description: str, elapsed: Optional[float], cached: bool) -> None:
    timing = "—  cached" if cached else f"{elapsed:.1f}s"
    _final_line(name, f"{description:<34}{timing:>10}")


def _announce_stage(name: str, message: str) -> None:
    """Immediate 'this is what's happening now' cue, printed before a stage
    starts doing (possibly silent, possibly minutes-long) work — so a wait
    never looks indistinguishable from a hang."""
    _live_line(name, message)


def _render_bar(fraction: float, width: int = 24) -> str:
    filled = int(max(0.0, min(fraction, 1.0)) * width)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {fraction * 100:5.1f}%"


def _throttled(min_interval: float = _LIVE_UPDATE_INTERVAL):
    """Wrap a callback so it only actually fires at most once per interval,
    always allowing the very first call through immediately."""
    last_call = [0.0]

    def decorator(fn):
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            if now - last_call[0] >= min_interval:
                last_call[0] = now
                fn(*args, **kwargs)

        return wrapper

    return decorator


def _make_transcribe_progress(total_duration: float):
    @_throttled()
    def on_progress(fraction: float) -> None:
        elapsed_str = _format_duration(fraction * total_duration)
        total_str = _format_duration(total_duration)
        _live_line("transcribe", f"{_render_bar(fraction)}  ({elapsed_str} / {total_str})")

    return on_progress


def _make_synthesize_progress():
    start = time.monotonic()

    @_throttled()
    def on_progress(phase: str, chars: int) -> None:
        elapsed = time.monotonic() - start
        verb = "thinking" if phase == "thinking" else "writing"
        _live_line("synthesize", f"{verb}... ({chars:,} chars, {elapsed:.0f}s elapsed)")

    return on_progress


class TerminalReporter:
    """Reporter that renders the same live stage-by-stage terminal output
    `_run_build` used to produce directly, now driven by pipeline.run_build."""

    def __init__(self) -> None:
        self._synth_start: Optional[float] = None

    def stage_starting(self, name: str, message: str) -> None:
        if name == "synthesize":
            self._synth_start = time.monotonic()
        _announce_stage(name, message)

    def stage_skipped(self, name: str, message: str) -> None:
        _final_line(name, message)

    def stage_done(self, name: str, description: str, elapsed: Optional[float]) -> None:
        _print_stage(name, description, elapsed, cached=elapsed is None)

    @_throttled()
    def transcribe_progress(self, fraction: float, total_duration: float) -> None:
        elapsed_str = _format_duration(fraction * total_duration)
        total_str = _format_duration(total_duration)
        _live_line("transcribe", f"{_render_bar(fraction)}  ({elapsed_str} / {total_str})")

    @_throttled()
    def synthesize_progress(self, phase: str, chars: int) -> None:
        elapsed = time.monotonic() - self._synth_start if self._synth_start is not None else 0.0
        verb = "thinking" if phase == "thinking" else "writing"
        _live_line("synthesize", f"{verb}... ({chars:,} chars, {elapsed:.0f}s elapsed)")

    def emit_done(self, path: Path) -> None:
        typer.echo(f"{'emit':<12}{str(path)}")


def _run_build(
    *,
    audio: Path,
    deck: Optional[Path],
    notes: Optional[Path],
    assets: list[Path],
    out: Path,
    course_code: str,
    number: int,
    course_name: Optional[str],
    instructor: Optional[str],
    force: bool,
    no_cache: bool,
    cfg: Config,
) -> Path:
    try:
        return _pipeline_run_build(
            audio=audio, deck=deck, notes=notes, assets=assets, out=out,
            course_code=course_code, number=number,
            course_name=course_name, instructor=instructor,
            force=force, no_cache=no_cache, cfg=cfg,
            reporter=TerminalReporter(),
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def build(
    course: Optional[str] = typer.Argument(None, help="Course code, e.g. COSC-4V88 (with a number, resolves via course.toml)."),
    number: Optional[int] = typer.Argument(None, help="Lecture number within the course."),
    audio: Optional[Path] = typer.Option(None, "--audio", help="Explicit path to the source audio file."),
    deck: Optional[Path] = typer.Option(None, "--deck", help="Explicit path to the slide deck."),
    notes: Optional[Path] = typer.Option(None, "--notes", help="Explicit path to your own notes (.md or .pdf)."),
    assets: list[Path] = typer.Option([], "--assets", help="Additional supplementary files (.md, .pdf, .txt, ...) as extra context. Repeatable."),
    out: Optional[Path] = typer.Option(None, "--out", help="Explicit path to write the generated notes to."),
    force: bool = typer.Option(False, "--force", help="Re-run every stage, ignoring the cache."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Don't read or write the cache for this run."),
) -> None:
    """Build lecture notes, either from explicit paths or a course + lecture number."""
    cfg = load_config()

    if course is not None and number is not None:
        try:
            course_root, info = _find_course(cfg.library_root, course)
        except CourseNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
        pattern = info.get("pattern", "Week{n}")
        try:
            resolved_audio, resolved_deck, resolved_notes, resolved_out = _resolve_course_paths(course_root, pattern, number)
        except CourseNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
        _run_build(
            audio=resolved_audio, deck=resolved_deck, notes=resolved_notes, assets=assets, out=resolved_out,
            course_code=info.get("code", course), number=number,
            course_name=info.get("name"), instructor=info.get("instructor"),
            force=force, no_cache=no_cache, cfg=cfg,
        )
        return

    if audio is None or out is None:
        typer.echo(
            "Provide either a course and lecture number (`notes build COSC-4V88 1`), "
            "or explicit paths (`notes build --audio ... --out ...`).",
            err=True,
        )
        raise typer.Exit(1)

    course_code = out.parent.name
    lecture_number = _guess_number(out.stem)
    _run_build(
        audio=audio, deck=deck, notes=notes, assets=assets, out=out,
        course_code=course_code, number=lecture_number,
        course_name=None, instructor=None,
        force=force, no_cache=no_cache, cfg=cfg,
    )


@app.command()
def append(
    audio: Path = typer.Option(..., "--audio", help="The new (continuation) audio segment."),
    note: Path = typer.Option(..., "--note", help="The existing lecture notes file to extend."),
    deck: Optional[Path] = typer.Option(None, "--deck", help="Slide deck (recommended, in case the continuation covers new slides)."),
    notes: Optional[Path] = typer.Option(None, "--notes", help="Your own notes for this continuation, if any (.md or .pdf)."),
    assets: list[Path] = typer.Option([], "--assets", help="Additional supplementary files (.md, .pdf, .txt, ...) as extra context. Repeatable."),
    out: Optional[Path] = typer.Option(None, "--out", help="Defaults to overwriting --note in place."),
    force: bool = typer.Option(False, "--force", help="Re-run every stage, ignoring the cache."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Don't read or write the cache for this run."),
) -> None:
    """Merge a continuation recording into an already-generated lecture note
    (for lectures recorded in multiple parts)."""
    cfg = load_config()
    out = out or note

    if not note.exists():
        typer.echo(f"Existing notes file not found: {note}", err=True)
        raise typer.Exit(1)

    old_meta, existing_markdown = _split_frontmatter(note.read_text())

    cache_root = out.parent / ".cache"
    store = Store(cfg.db_path)
    cache = Cache(store, cache_root)
    lecture_id = f"{out.parent.name}-{_guess_number(out.stem)}"
    use_cache = not no_cache and not force

    # -- slides --
    deck_obj: Optional[Deck] = None
    if deck is not None:
        key = cache_key("slides", _STAGE_VERSION, [deck], {})
        cached_path = cache.get(key) if use_cache else None
        if cached_path is not None:
            deck_obj = Deck.model_validate_json(cached_path.read_text())
            _print_stage("slides", f"{len(deck_obj.slides)} slides", None, cached=True)
        else:
            _announce_stage("slides", "extracting deck...")
            start = time.time()
            deck_obj = extract_slides(deck)
            elapsed = time.time() - start
            if not no_cache:
                cache.put(key, deck_obj.model_dump_json(), stage="slides", lecture_id=lecture_id)
            _print_stage("slides", f"{len(deck_obj.slides)} slides", elapsed, cached=False)

    # -- audio (new segment only) --
    audio_key = cache_key("audio", _STAGE_VERSION, [audio], {})
    wav_path = cache.cache_root / f"{audio_key}.wav"
    cached_wav = cache.get(audio_key) if use_cache else None
    if cached_wav is not None:
        new_duration = wav_duration(cached_wav)
        wav_path = cached_wav
        _print_stage("audio", f"{new_duration:.1f}s -> wav", None, cached=True)
    else:
        _announce_stage("audio", "normalizing + resampling (usually under a minute)...")
        start = time.time()
        new_duration = audio_prepare(audio, wav_path)
        elapsed = time.time() - start
        if not no_cache:
            cache.store.put_cache_entry(audio_key, stage="audio", lecture_id=lecture_id, payload_path=wav_path, meta={})
        _print_stage("audio", f"{new_duration:.1f}s -> wav", elapsed, cached=False)

    # -- transcribe (new segment only) --
    transcribe_key = cache_key("transcribe", _STAGE_VERSION, [wav_path], {})
    cached_transcript = cache.get(transcribe_key) if use_cache else None
    if cached_transcript is not None:
        new_transcript = Transcript.model_validate_json(cached_transcript.read_text())
        _print_stage("transcribe", f"{len(new_transcript.segments)} segments ({cfg.transcriber})", None, cached=True)
    else:
        _announce_stage("transcribe", f"running {cfg.transcriber} on {_format_duration(new_duration)} of audio...")
        start = time.time()
        new_transcript = get_transcriber(cfg).transcribe(wav_path, on_progress=_make_transcribe_progress(new_duration))
        elapsed = time.time() - start
        if not no_cache:
            cache.put(transcribe_key, new_transcript.model_dump_json(), stage="transcribe", lecture_id=lecture_id)
            cached_transcript = cache.get(transcribe_key)
        _print_stage("transcribe", f"{len(new_transcript.segments)} segments ({cfg.transcriber})", elapsed, cached=False)

    # -- synthesize (merge) --
    synth_inputs = [p for p in [note, deck, cached_transcript, notes, *assets] if p is not None]
    synth_key = cache_key("synthesize_append", _STAGE_VERSION, synth_inputs, {"model": cfg.model})
    cached_markdown = cache.get(synth_key) if use_cache else None
    if cached_markdown is not None:
        markdown = cached_markdown.read_text()
        _print_stage("synthesize", cfg.model, None, cached=True)
    else:
        _announce_stage("synthesize", f"calling {cfg.model}...")
        start = time.time()
        my_notes_text = read_text_file(notes) if notes is not None else None
        markdown = synthesize_append(
            existing_notes=existing_markdown, deck=deck_obj, new_transcript=new_transcript,
            my_notes=my_notes_text, extras=read_assets(assets), course=out.parent.name, cfg=cfg,
            on_progress=_make_synthesize_progress(),
        )
        elapsed = time.time() - start
        if not no_cache:
            cache.put(synth_key, markdown, stage="synthesize_append", lecture_id=lecture_id)
        _print_stage("synthesize", cfg.model, elapsed, cached=False)

    # -- emit --
    lecture = Lecture(
        id=lecture_id, course=old_meta.get("course", out.parent.name),
        number=_guess_number(out.stem), title=old_meta.get("title"),
        date=old_meta.get("date"), dir=out.parent,
    )
    combined_duration = _parse_duration(str(old_meta["duration"])) + new_duration if "duration" in old_meta else new_duration
    meta = dict(old_meta)
    meta["source_audio"] = f"{old_meta.get('source_audio', '?')} + {audio.name}"
    if deck is not None:
        meta["source_deck"] = deck.name
    meta["duration"] = _format_duration(combined_duration)
    meta["transcript_engine"] = new_transcript.engine
    meta["generated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta["model"] = cfg.model

    result = emit_notes(markdown, lecture, meta, out)
    store.add_lecture(lecture)
    store.add_note(lecture_id, result, model=cfg.model, prompt_version=_STAGE_VERSION)
    typer.echo(f"{'emit':<12}{str(result)}")


@app.command()
def transcribe(audio: Path = typer.Argument(..., help="Path to the source audio file.")) -> None:
    """Transcribe an audio file and print the timestamped transcript. Transcript only — no deck, no notes, no synthesis."""
    cfg = load_config()
    cache_root = audio.parent / ".cache"
    store = Store(cfg.db_path)
    cache = Cache(store, cache_root)

    audio_key = cache_key("audio", _STAGE_VERSION, [audio], {})
    wav_path = cache.cache_root / f"{audio_key}.wav"
    cached_wav = cache.get(audio_key)
    if cached_wav is not None:
        wav_path = cached_wav
    else:
        audio_prepare(audio, wav_path)
        cache.store.put_cache_entry(audio_key, stage="audio", lecture_id="adhoc", payload_path=wav_path, meta={})

    transcribe_key = cache_key("transcribe", _STAGE_VERSION, [wav_path], {})
    cached_transcript = cache.get(transcribe_key)
    if cached_transcript is not None:
        transcript = Transcript.model_validate_json(cached_transcript.read_text())
    else:
        transcript = get_transcriber(cfg).transcribe(wav_path)
        cache.put(transcribe_key, transcript.model_dump_json(), stage="transcribe", lecture_id="adhoc")

    typer.echo(transcript.to_timestamped_text())


@app.command()
def ls(course: Optional[str] = typer.Argument(None, help="Filter to a single course code.")) -> None:
    """List lectures known to the store."""
    cfg = load_config()
    store = Store(cfg.db_path)
    lectures = store.list_lectures(course)
    if not lectures:
        typer.echo("No lectures found.")
        return
    for lecture in lectures:
        title = f" — {lecture.title}" if lecture.title else ""
        typer.echo(f"{lecture.course:<14}{lecture.number:<4}{lecture.id:<16}{title}")


if __name__ == "__main__":
    app()
