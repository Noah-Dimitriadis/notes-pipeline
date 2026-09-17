from __future__ import annotations

from pathlib import Path
from typing import Callable

import anthropic

from ..config import Config
from ..models import Deck, Transcript

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_BASE_PROMPT_PATH = _PROMPTS_DIR / "base.md"
_APPEND_PROMPT_PATH = _PROMPTS_DIR / "append.md"
_COURSE_PROMPTS_DIR = _PROMPTS_DIR / "courses"

# (phase, cumulative_chars_in_that_phase) -- phase is "thinking" or "writing"
ProgressCallback = Callable[[str, int], None]


class SynthesisError(RuntimeError):
    """Raised for a clear, single-sentence synthesis failure."""


def synthesize(
    deck: Deck | None,
    transcript: Transcript,
    my_notes: str | None,
    extras: list[str],
    course: str,
    cfg: Config,
    on_progress: ProgressCallback | None = None,
    api_key: str | None = None,
) -> str:
    """Call Claude to turn deck + transcript + notes into the markdown body
    of a lecture note file (no YAML frontmatter — that's emit's job).

    `api_key`, if given, is used instead of `cfg.anthropic_api_key` — the
    hosted service (M13) passes each user's own key here rather than a
    global one baked into `cfg`."""
    system_prompt = _BASE_PROMPT_PATH.read_text()
    course_override = _load_course_override(course)
    if course_override:
        system_prompt = f"{system_prompt}\n\n---\n\n{course_override}"

    deck_block = _render_deck(deck)
    transcript_block = transcript.to_timestamped_text()
    my_notes_block = my_notes or "(The student did not provide notes for this lecture.)"
    instruction_block = _render_instruction_block(extras)

    client = anthropic.Anthropic(api_key=api_key or cfg.anthropic_api_key.get_secret_value())
    content_blocks = [
        {"type": "text", "text": deck_block, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": transcript_block, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": my_notes_block},
        {"type": "text", "text": instruction_block},
    ]
    message = _stream_completion(client, cfg.model, system_prompt, content_blocks, on_progress)
    return _extract_markdown(message)


def synthesize_append(
    existing_notes: str,
    deck: Deck | None,
    new_transcript: Transcript,
    my_notes: str | None,
    extras: list[str],
    course: str,
    cfg: Config,
    on_progress: ProgressCallback | None = None,
    api_key: str | None = None,
) -> str:
    """Merge a continuation recording (a lecture split across multiple audio
    files) into an already-generated set of notes. Returns the complete,
    updated markdown body (no frontmatter — that's emit's job)."""
    system_prompt = f"{_BASE_PROMPT_PATH.read_text()}\n\n---\n\n{_APPEND_PROMPT_PATH.read_text()}"
    course_override = _load_course_override(course)
    if course_override:
        system_prompt = f"{system_prompt}\n\n---\n\n{course_override}"

    deck_block = _render_deck(deck)
    existing_notes_block = f"EXISTING NOTES (from earlier part(s) of this lecture):\n\n{existing_notes.strip()}"
    new_transcript_block = (
        "NEW TRANSCRIPT (a continuation of the same lecture, recorded separately — "
        f"timestamps restart from 00:00):\n\n{new_transcript.to_timestamped_text()}"
    )
    my_notes_block = my_notes or "(The student did not provide additional notes for this part.)"
    instruction_block = _render_append_instruction_block(extras)

    client = anthropic.Anthropic(api_key=api_key or cfg.anthropic_api_key.get_secret_value())
    content_blocks = [
        {"type": "text", "text": deck_block, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": existing_notes_block, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": new_transcript_block, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": my_notes_block},
        {"type": "text", "text": instruction_block},
    ]
    message = _stream_completion(client, cfg.model, system_prompt, content_blocks, on_progress)
    return _extract_markdown(message)


def _stream_completion(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    content_blocks: list[dict],
    on_progress: ProgressCallback | None,
) -> anthropic.types.Message:
    try:
        with client.messages.stream(
            model=model,
            max_tokens=32000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[{"role": "user", "content": content_blocks}],
        ) as stream:
            thinking_chars = 0
            text_chars = 0
            for event in stream:
                if on_progress is None:
                    continue
                if event.type == "thinking":
                    thinking_chars += len(event.thinking)
                    on_progress("thinking", thinking_chars)
                elif event.type == "text":
                    text_chars += len(event.text)
                    on_progress("writing", text_chars)
            return stream.get_final_message()
    except anthropic.AnthropicError as exc:
        raise SynthesisError(f"Claude API request failed: {exc}") from exc


def _render_append_instruction_block(extras: list[str]) -> str:
    lines = [
        "Merge the new transcript into the existing notes now, following the "
        "append instructions and the four rules in the system prompt exactly. "
        "Output only the complete, updated markdown body, starting with "
        "'## TL;DR' — no YAML frontmatter, no wrapping code fence, no preamble."
    ]
    if extras:
        lines.append("\nAdditional material provided:\n\n" + "\n\n".join(extras))
    return "\n".join(lines)


def _render_deck(deck: Deck | None) -> str:
    if deck is None:
        return "(No slide deck was provided for this lecture.)"

    parts = [f"Slide deck source: {deck.source.name}"]
    for slide in deck.slides:
        header = f"### Slide {slide.index}"
        if slide.title:
            header += f" — {slide.title}"
        block = [header]
        if slide.body.strip():
            block.append(slide.body.strip())
        if slide.notes:
            block.append(f"[Speaker notes: {slide.notes.strip()}]")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _load_course_override(course: str) -> str:
    override_path = _COURSE_PROMPTS_DIR / f"{course}.md"
    if override_path.exists():
        return override_path.read_text()
    return ""


def _render_instruction_block(extras: list[str]) -> str:
    lines = [
        "Using the slide deck, transcript, and the student's own notes above, "
        "write the lecture notes now, following the output format and the "
        "four rules specified in the system prompt exactly. Output only the "
        "markdown body, starting with '## TL;DR' — no YAML frontmatter, no "
        "wrapping code fence, no preamble."
    ]
    if extras:
        lines.append("\nAdditional material provided:\n\n" + "\n\n".join(extras))
    return "\n".join(lines)


def _extract_markdown(message: anthropic.types.Message) -> str:
    text_parts = [block.text for block in message.content if block.type == "text"]
    return _strip_code_fence("".join(text_parts).strip())


def _strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text
