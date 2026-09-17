from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path


class AudioError(RuntimeError):
    """Raised for a clear, single-sentence audio-prep failure."""


def prepare(src: Path, dst: Path) -> float:
    """Convert src to a canonical 16kHz mono PCM wav at dst, loudness-normalised
    for quiet lecture-hall recordings. Returns the resulting duration in seconds."""
    return prepare_many([src], dst)


def prepare_many(srcs: list[Path], dst: Path) -> float:
    """Concatenate one or more raw audio files (in the given order) into a
    single canonical 16kHz mono PCM wav at dst, loudness-normalised.
    `prepare` is just this with one input — a lecture recorded in multiple
    parts (recorder paused/restarted mid-lecture) is otherwise the same
    pipeline input as a single continuous file, just concatenated first."""
    if not srcs:
        raise AudioError("At least one audio file is required.")
    if shutil.which("ffmpeg") is None:
        raise AudioError("ffmpeg not found on PATH. Install it, e.g. `brew install ffmpeg`.")

    dst.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-y"]
    for src in srcs:
        args += ["-i", str(src)]
    # `concat=n=1` for a single input is just the identity — one filter
    # graph handles both cases, so there's no separate code path to drift
    # between "one file" and "several" the way there would be with -af
    # (single-input only) for the common case and -filter_complex for the
    # rest.
    labels = "".join(f"[{i}:a]" for i in range(len(srcs)))
    filter_complex = f"{labels}concat=n={len(srcs)}:v=0:a=1[cat];[cat]loudnorm=I=-16:TP=-1.5:LRA=11[out]"
    args += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(dst),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown ffmpeg error"
        names = ", ".join(s.name for s in srcs)
        raise AudioError(f"ffmpeg failed to process {names}: {detail}")

    return wav_duration(dst)


def wav_duration(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as f:
        return f.getnframes() / f.getframerate()
