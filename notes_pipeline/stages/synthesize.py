from __future__ import annotations

from pathlib import Path

import anthropic

from ..config import Config
from ..models import Deck, Transcript

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_BASE_PROMPT_PATH = _PROMPTS_DIR / "base.md"
_COURSE_PROMPTS_DIR = _PROMPTS_DIR / "courses"


class SynthesisError(RuntimeError):
    """Raised for a clear, single-sentence synthesis failure."""


def synthesize(
    deck: Deck | None,
    transcript: Transcript,
    my_notes: str | None,
    extras: list[str],
    course: str,
    cfg: Config,
) -> str:
    """Call Claude to turn deck + transcript + notes into the markdown body
    of a lecture note file (no YAML frontmatter — that's emit's job)."""
    system_prompt = _BASE_PROMPT_PATH.read_text()
    course_override = _load_course_override(course)
    if course_override:
        system_prompt = f"{system_prompt}\n\n---\n\n{course_override}"

    deck_block = _render_deck(deck)
    transcript_block = transcript.to_timestamped_text()
    my_notes_block = my_notes or "(The student did not provide notes for this lecture.)"
    instruction_block = _render_instruction_block(extras)

    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key.get_secret_value())

    try:
        with client.messages.stream(
            model=cfg.model,
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
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": deck_block,
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        },
                        {
                            "type": "text",
                            "text": transcript_block,
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        },
                        {"type": "text", "text": my_notes_block},
                        {"type": "text", "text": instruction_block},
                    ],
                }
            ],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AnthropicError as exc:
        raise SynthesisError(f"Claude API request failed: {exc}") from exc

    return _extract_markdown(message)


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
