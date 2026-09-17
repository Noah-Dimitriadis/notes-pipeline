from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Callable, Protocol

from ..config import Config
from ..models import Segment, Transcript
from .audio import wav_duration

_MIN_MEAN_CONFIDENCE = 0.5
_MAX_IDENTICAL_RUN = 20
_WORD_STUTTER_RE = re.compile(r"\b(\w+)\b(?:[\s,]+\1\b){3,}", re.IGNORECASE)
_SEGMENT_END_RE = re.compile(r"-->\s*(\d{2}):(\d{2}):(\d{2})\.\d{3}\]")

ProgressCallback = Callable[[float], None]


class TranscriberError(RuntimeError):
    """Raised for a clear, single-sentence transcription failure."""


class TranscriptQualityWarning(UserWarning):
    """Issued when a transcript shows signs of a known engine degeneration mode."""


class Transcriber(Protocol):
    def transcribe(
        self, wav: Path, *, vocabulary: list[str] | None = None, on_progress: ProgressCallback | None = None
    ) -> Transcript: ...


def _gpu_available() -> bool:
    """Best-effort probe for whether whisper-cli has a GPU backend to reach
    for. On macOS this is always True: whisper-cli's Metal backend is
    self-initializing (it doesn't need a device file), and every Mac this
    project runs on already gets working GPU-accelerated transcription
    today (PLAN.md §2's Benchmark A/B) — nothing to detect. On Linux
    (treehouse's real deployment, and this same image run locally in
    Docker Desktop's Linux VM), whisper.cpp's Vulkan backend needs an
    actual `/dev/dri` render node; treehouse mounts one in, a plain
    `docker compose up` on a Mac with no such device does not — so the
    presence of a render node is what actually distinguishes "has a GPU to
    use" from "doesn't" on that platform."""
    if sys.platform == "darwin":
        return True
    dri = Path("/dev/dri")
    return dri.is_dir() and any(dri.glob("render*"))


def get_transcriber(cfg: Config) -> Transcriber:
    if cfg.transcriber == "whisper":
        return WhisperTranscriber(cfg)
    if cfg.transcriber == "remote":
        return RemoteTranscriber(cfg)
    raise TranscriberError(f"Unknown transcriber: {cfg.transcriber!r}")


class WhisperTranscriber:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def transcribe(
        self, wav: Path, *, vocabulary: list[str] | None = None, on_progress: ProgressCallback | None = None
    ) -> Transcript:
        # `vocabulary` is accepted for Transcriber-protocol compatibility but
        # deliberately unused here. -mc 0 and --prompt are mutually exclusive
        # (max-context zero means no prompt tokens reach the decoder at all),
        # and dropping -mc 0 to let seeding through was tested directly
        # against Benchmark B (turbo, default max-context, real deck
        # vocabulary as --prompt): it produced a 377-segment repetition loop
        # and *reduced* jargon recall everywhere else in the transcript too
        # (e.g. UNESCO 2->0, accountability 4->1, beneficence 3->1 vs the
        # -mc 0 baseline) — not an isolated failure a retry could contain.
        # Jargon correction stays M6's job (Claude reconciling the deck
        # against the transcript), exactly as M3 already specifies.
        del vocabulary
        total_duration = wav_duration(wav)

        use_cpu = self.cfg.whisper_device == "cpu" or (
            self.cfg.whisper_device == "auto" and not _gpu_available()
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            outbase = Path(tmpdir) / "out"
            args = [
                str(self.cfg.whisper_bin),
                "-m", str(self.cfg.whisper_model),
                "-f", str(wav),
                "-l", "en",
                "-t", str(self.cfg.whisper_threads),
                "-mc", "0",
                "-oj",
                "-of", str(outbase),
            ]
            if use_cpu:
                args.append("-ng")
            # No -np here: whisper-cli's stdout then carries one clean
            # "[HH:MM:SS.mmm --> HH:MM:SS.mmm]  text" line per segment as it
            # works (backend/timing noise goes to stderr), which is what lets
            # us report live progress against the known audio duration
            # instead of going silent for minutes. stderr is redirected to a
            # real file rather than left as an undrained PIPE (which risks
            # deadlocking the child once its OS buffer fills) or DEVNULL
            # (which would lose the detail needed for a useful error message).
            stderr_path = Path(tmpdir) / "stderr.log"
            try:
                with open(stderr_path, "w") as stderr_file:
                    process = subprocess.Popen(
                        args, stdout=subprocess.PIPE, stderr=stderr_file, text=True, bufsize=1,
                    )
                    assert process.stdout is not None
                    for line in process.stdout:
                        if on_progress is None:
                            continue
                        match = _SEGMENT_END_RE.search(line)
                        if match:
                            h, m, s = (int(g) for g in match.groups())
                            elapsed = h * 3600 + m * 60 + s
                            on_progress(min(elapsed / total_duration, 1.0) if total_duration else 0.0)
                    returncode = process.wait()
            except FileNotFoundError:
                raise TranscriberError(
                    f"whisper-cli not found at {self.cfg.whisper_bin!r}. "
                    "Check whisper_bin in your config, or install whisper.cpp."
                ) from None

            if returncode != 0:
                stderr_text = stderr_path.read_text().strip()
                detail = stderr_text.splitlines()[-1] if stderr_text else "unknown whisper-cli error"
                raise TranscriberError(f"whisper-cli failed on {wav}: {detail}")

            data = json.loads(outbase.with_suffix(".json").read_text())

        segments = [
            Segment(
                start=seg["offsets"]["from"] / 1000,
                end=seg["offsets"]["to"] / 1000,
                text=seg["text"].strip(),
            )
            for seg in data["transcription"]
        ]
        transcript = Transcript(
            segments=segments,
            duration=wav_duration(wav),
            engine="whisper large-v3-turbo",
            language=data.get("result", {}).get("language", "en"),
        )
        check_quality(transcript)
        return transcript


class RemoteTranscriber:
    def __init__(self, cfg: Config):
        if not cfg.remote_url:
            raise TranscriberError("transcriber is set to 'remote' but no remote_url is configured.")
        self.cfg = cfg

    def transcribe(
        self, wav: Path, *, vocabulary: list[str] | None = None, on_progress: ProgressCallback | None = None
    ) -> Transcript:
        del on_progress  # no live progress signal from a remote HTTP call yet
        import httpx2

        with open(wav, "rb") as f:
            response = httpx2.post(
                self.cfg.remote_url,
                files={"audio": (wav.name, f, "audio/wav")},
                data={"vocabulary": json.dumps(vocabulary)} if vocabulary else None,
                timeout=None,
            )
        response.raise_for_status()
        data = response.json()

        segments = [Segment(**seg) for seg in data["segments"]]
        transcript = Transcript(
            segments=segments,
            duration=data.get("duration", wav_duration(wav)),
            engine=data.get("engine", "remote"),
            language=data.get("language", "en"),
        )
        check_quality(transcript)
        return transcript


def check_quality(transcript: Transcript) -> None:
    """Issue a TranscriptQualityWarning if the transcript shows signs of a
    known engine degeneration mode. Catch a bad transcript here, not by
    reading a hallucinated note file three weeks later."""
    reasons: list[str] = []

    run_len = 1
    max_run = 1
    for i in range(1, len(transcript.segments)):
        if transcript.segments[i].text.strip() == transcript.segments[i - 1].text.strip():
            run_len += 1
            max_run = max(max_run, run_len)
        else:
            run_len = 1
    if max_run > _MAX_IDENTICAL_RUN:
        reasons.append(f"{max_run} consecutive identical segments (repetition loop)")

    for seg in transcript.segments:
        match = _WORD_STUTTER_RE.search(seg.text)
        if match:
            reasons.append(f"word repeated 4+ times in a row at {seg.start:.1f}s: {seg.text!r}")
            break

    confidences = [s.confidence for s in transcript.segments if s.confidence is not None]
    if confidences:
        mean_confidence = sum(confidences) / len(confidences)
        if mean_confidence < _MIN_MEAN_CONFIDENCE:
            reasons.append(f"mean confidence {mean_confidence:.2f} below {_MIN_MEAN_CONFIDENCE}")

    if reasons:
        warnings.warn(
            "Transcript quality check failed: " + "; ".join(reasons),
            TranscriptQualityWarning,
            stacklevel=2,
        )
