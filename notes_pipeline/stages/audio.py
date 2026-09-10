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
    if shutil.which("ffmpeg") is None:
        raise AudioError("ffmpeg not found on PATH. Install it, e.g. `brew install ffmpeg`.")

    dst.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(dst),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown ffmpeg error"
        raise AudioError(f"ffmpeg failed to process {src}: {detail}")

    return _wav_duration(dst)


def _wav_duration(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as f:
        return f.getnframes() / f.getframerate()
