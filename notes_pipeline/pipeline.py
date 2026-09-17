from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from typing import Optional, Protocol

from .cache import Cache, cache_key
from .config import Config
from .models import Deck, Lecture, Transcript
from .stages.audio import prepare as audio_prepare
from .stages.audio import wav_duration
from .stages.emit import emit as emit_notes
from .stages.slides import extract_many as extract_slides
from .stages.slides import pdf_to_text
from .stages.synthesize import synthesize
from .stages.transcribe import get_transcriber
from .store import Store

STAGE_VERSION = "v1"


class Reporter(Protocol):
    """Stage-progress sink. `build`'s only dependency on presentation —
    the terminal (cli.py) and a background job (mcp_server.py) each supply
    their own so the pipeline logic itself stays UI-agnostic."""

    def stage_starting(self, name: str, message: str) -> None: ...
    def stage_skipped(self, name: str, message: str) -> None: ...
    def stage_done(self, name: str, description: str, elapsed: Optional[float]) -> None: ...
    def transcribe_progress(self, fraction: float, total_duration: float) -> None: ...
    def synthesize_progress(self, phase: str, chars: int) -> None: ...
    def emit_done(self, path: Path) -> None: ...


class NullReporter:
    def stage_starting(self, name: str, message: str) -> None:
        pass

    def stage_skipped(self, name: str, message: str) -> None:
        pass

    def stage_done(self, name: str, description: str, elapsed: Optional[float]) -> None:
        pass

    def transcribe_progress(self, fraction: float, total_duration: float) -> None:
        pass

    def synthesize_progress(self, phase: str, chars: int) -> None:
        pass

    def emit_done(self, path: Path) -> None:
        pass


def read_text_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return pdf_to_text(path)
    return path.read_text()


def read_assets(paths: list[Path]) -> list[str]:
    return [f"--- {p.name} ---\n\n{read_text_file(p)}" for p in paths]


def run_build(
    *,
    audio: Path,
    deck: list[Path],
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
    reporter: Optional[Reporter] = None,
    api_key: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Path:
    """Run the full slides -> audio -> transcribe -> synthesize -> emit
    pipeline for one lecture, reporting stage-by-stage progress through
    `reporter`. Shared by the CLI's `build` command, the MCP server's
    `build_lecture` job runner, and the hosted service's (M13) build worker,
    so caching and stage behaviour can't drift between entry points.

    `api_key`, if given, is used for the synthesis call instead of
    `cfg.anthropic_api_key` — the hosted service has no single global key
    (§14.1: "each user supplies their own"), so its build worker always
    passes the uploading user's own decrypted key here rather than relying
    on `cfg`. `user_id` similarly scopes the resulting `lectures`/`notes`
    rows (§14.3); both are no-ops for the personal CLI/MCP entry points."""
    reporter = reporter or NullReporter()

    if notes is not None and notes.resolve() == out.resolve():
        raise ValueError("The output path must not equal the input notes path.")

    cache_root = out.parent / ".cache"
    store = Store(cfg.db_path)
    cache = Cache(store, cache_root)
    lecture_id = f"{course_code}-{number}"

    use_cache = not no_cache and not force

    # -- slides --
    deck_obj: Optional[Deck] = None
    if deck:
        key = cache_key("slides", STAGE_VERSION, deck, {})
        cached_path = cache.get(key) if use_cache else None
        if cached_path is not None:
            deck_obj = Deck.model_validate_json(cached_path.read_text())
            reporter.stage_done("slides", f"{len(deck_obj.slides)} slides", None)
        else:
            reporter.stage_starting("slides", "extracting deck...")
            start = time.time()
            deck_obj = extract_slides(deck)
            elapsed = time.time() - start
            if not no_cache:
                cache.put(key, deck_obj.model_dump_json(), stage="slides", lecture_id=lecture_id)
            reporter.stage_done("slides", f"{len(deck_obj.slides)} slides", elapsed)
    else:
        reporter.stage_skipped("slides", "(no deck provided)")

    # -- audio --
    audio_key = cache_key("audio", STAGE_VERSION, [audio], {})
    wav_path = cache.cache_root / f"{audio_key}.wav"
    cached_wav = cache.get(audio_key) if use_cache else None
    if cached_wav is not None:
        duration = wav_duration(cached_wav)
        wav_path = cached_wav
        reporter.stage_done("audio", f"{duration:.1f}s -> wav", None)
    else:
        reporter.stage_starting("audio", "normalizing + resampling (usually under a minute)...")
        start = time.time()
        duration = audio_prepare(audio, wav_path)
        elapsed = time.time() - start
        if not no_cache:
            cache.store.put_cache_entry(audio_key, stage="audio", lecture_id=lecture_id, payload_path=wav_path, meta={})
        reporter.stage_done("audio", f"{duration:.1f}s -> wav", elapsed)

    # -- transcribe --
    transcribe_key = cache_key("transcribe", STAGE_VERSION, [wav_path], {})
    cached_transcript = cache.get(transcribe_key) if use_cache else None
    if cached_transcript is not None:
        transcript = Transcript.model_validate_json(cached_transcript.read_text())
        reporter.stage_done("transcribe", f"{len(transcript.segments)} segments ({cfg.transcriber})", None)
    else:
        reporter.stage_starting("transcribe", f"running {cfg.transcriber} on {duration:.0f}s of audio...")
        start = time.time()
        transcriber = get_transcriber(cfg)

        def on_transcribe_progress(fraction: float, _duration: float = duration) -> None:
            reporter.transcribe_progress(fraction, _duration)

        transcript = transcriber.transcribe(wav_path, on_progress=on_transcribe_progress)
        elapsed = time.time() - start
        if not no_cache:
            cache.put(transcribe_key, transcript.model_dump_json(), stage="transcribe", lecture_id=lecture_id)
            cached_transcript = cache.get(transcribe_key)
        reporter.stage_done("transcribe", f"{len(transcript.segments)} segments ({cfg.transcriber})", elapsed)

    # -- synthesize --
    synth_inputs = [p for p in [*deck, cached_transcript, notes, *assets] if p is not None]
    synth_key = cache_key("synthesize", STAGE_VERSION, synth_inputs, {"model": cfg.model})
    cached_markdown = cache.get(synth_key) if use_cache else None
    if cached_markdown is not None:
        markdown = cached_markdown.read_text()
        reporter.stage_done("synthesize", cfg.model, None)
    else:
        reporter.stage_starting("synthesize", f"calling {cfg.model}...")
        start = time.time()
        my_notes_text = read_text_file(notes) if notes is not None else None
        markdown = synthesize(
            deck=deck_obj, transcript=transcript, my_notes=my_notes_text,
            extras=read_assets(assets), course=course_code, cfg=cfg,
            on_progress=reporter.synthesize_progress,
            api_key=api_key,
        )
        elapsed = time.time() - start
        if not no_cache:
            cache.put(synth_key, markdown, stage="synthesize", lecture_id=lecture_id)
        reporter.stage_done("synthesize", cfg.model, elapsed)

    # -- emit --
    lecture = Lecture(
        id=lecture_id, course=course_code, number=number,
        title=None, date=date.today(), dir=out.parent,
    )
    meta = {
        "course": course_name or course_code,
        "instructor": instructor,
        "source_audio": audio.name,
        "source_deck": ", ".join(d.name for d in deck) if deck else None,
        "duration": f"{int(duration) // 60}:{int(duration) % 60:02d}",
        "transcript_engine": transcript.engine,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": cfg.model,
    }
    meta = {k: v for k, v in meta.items() if v is not None}
    result = emit_notes(markdown, lecture, meta, out)
    store.add_lecture(lecture, user_id=user_id)
    store.add_note(lecture_id, result, model=cfg.model, prompt_version=STAGE_VERSION, user_id=user_id)
    reporter.emit_done(result)
    return result
