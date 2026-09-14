# Lecture Notes Pipeline — Build Plan

Ingest a lecture recording, the slide deck, and my own notes; emit one
well-formed markdown file per lecture, good enough to be the *only* context
document a Claude Code study session needs.

Written to be built one module at a time. Each module below is scoped to a
single Sonnet session: it names what to create, the exact interface, and the
acceptance criteria that define done.

---

## 1. Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Where it runs | The Mac | Claude Code runs here; transcription is fast here; nothing else needs to be true |
| Transcription | whisper.cpp `large-v3-turbo` | Only engine tested that survives real lecture audio — see §2 |
| Synthesis | Claude API, `claude-opus-5` | 1M context deletes chunking, embeddings, and alignment from the design |
| Storage | SQLite + files on disk | Single-user, single-machine; the server's Postgres bought nothing |
| Interface | MCP over stdio, via FastMCP | Point Claude Code at a folder and say "synthesise this, then quiz me" |
| Notes destination | Local files, not the Obsidian vault | Vault refactor pending |
| Frontend | Deferred — spec'd in §10, not built | |
| Python | 3.13, venv + `pip` | Wheel availability |

**The core idea:** the deck is the skeleton, and the audio's unique value is
*what the professor said that is not on the slide.* Every design choice below
serves that. A note file that just restates the deck is worthless — I already
have the deck.

---

## 2. Measured facts

### Benchmark A — clean read speech (audiobook)

2694.32 s (44:55), 16 kHz mono. Reproducible: LibriVox
`heartofamystery_2005_librivox` chapters 02 + 04, concatenated,
`-ar 16000 -ac 1 -c:a pcm_s16le`.

| Engine | Hardware | Wall clock | Realtime | Words | Repetition loops |
|---|---|---|---|---|---|
| whisper large-v3-turbo | M3 Air (Metal) | 338.68 s | 7.96× | ~7,850 | none |
| whisper large-v3-turbo | GTX 1060 (CUDA) | 274.22 s | 9.83× | ~7,850 | none |
| whisper large-v3 | GTX 1060 (CUDA) | 1098.29 s | 2.45× | 9,628 | **913** |

### Benchmark B — real lecture (the one that decides it)

3833.98 s (63:54) recorded on an iPhone in a Brock lecture hall. Accented
lecturer, room reverb, an AI-ethics topic with heavy domain vocabulary.

| Engine | Wall clock | Realtime | Words | Domain terms hit | Stutter runs |
|---|---|---|---|---|---|
| whisper large-v3-turbo (M3 Air) | 374.25 s | 10.2× | 7,874 | 125 | 0 |

Covers 94.7% of the audio. **This is the reference corpus** — it lives in
`testlecture/` with its transcripts. Re-run any engine change against it.

### The three findings that shaped the plan

1. **`large-v3` is not the safe default.** It fell into a repetition loop and
   emitted one line 913 times — over half its output hallucinated. The distilled
   `turbo` model was cleaner *and* 4× faster. Use `turbo`, and pass `-mc 0`.
2. **Clean-audio benchmarks do not predict lecture-hall performance.** Any future
   engine comparison must be run on Benchmark B, not Benchmark A. See the
   rejection note below for how badly A can mislead.
3. **Transcription costs ~6 minutes per lecture** on the Mac and is the slowest
   stage by far. This is what makes the stage cache in M2 load-bearing, and what
   the 9060 XT box would improve (§10).

### Rejected: Apple `SpeechAnalyzer` (macOS 26)

Evaluated and dropped — recorded so it isn't re-litigated.

On Benchmark A it looked like a clean win: 41.19 s, 65.4× realtime, 7,875 words,
matching whisper at 8× the speed. On Benchmark B it collapsed. It ran in 55.89 s
but hit only **50 domain terms to whisper's 125**, and produced *more* words while
carrying *less* information — it fills uncertainty with plausible-sounding filler.
It scored zero occurrences of `UNESCO`, `artificial intelligence`,
`accountability`, `sustainability`, `human oversight`, `framework`, and
`responsible AI` — the last being the lecture's actual subject, which whisper
caught 5 times. It also produced 18 single-word stutter runs against whisper's 0:

| Apple | whisper |
|---|---|
| "both benefits and ants can affect many people" | "both benefits and harms can affect many people very quickly" |
| "There's one, China, one, that's Nesco, at their house." | "There is one in China that UNESCO has their own." |
| "I, I, I, I, I, I, I, I, I, I, I, I" | "Unfortunately, I didn't hit that button" |

---

## 3. Architecture

```
audio ──► [M4 audio prep] ──► wav 16k mono ──┐
                                             ├─► [M5 transcribe] ──► Transcript (timestamped segments)
deck  ──► [M3 slides] ──► Deck + vocabulary ─┘                              │
                              │                                             │
my notes, extras ─────────────┴─────────────────────────────────────────────┤
                                                                            ▼
                                                          [M6 synthesize] ──► markdown
                                                                            │
                                                              [M7 emit] ────► out/notes.md
```

Every stage is wrapped by the content-hash cache (M2). Re-running after editing
only the prompt re-does synthesis alone — transcription, the slow step, is never
repeated for unchanged audio. This is what makes prompt iteration bearable.

---

## 4. Repo layout

```
notes-pipeline/
  PLAN.md
  pyproject.toml
  notes_pipeline/
    __init__.py
    config.py            # M0
    models.py            # M1
    store.py  cache.py   # M2
    library.py            # course.toml discovery + path resolution, shared by M8 and M9
    pipeline.py            # the build pipeline itself (M3-M7), UI-agnostic; M8 and M9 each supply a Reporter
    stages/
      slides.py          # M3
      audio.py           # M4
      transcribe.py      # M5
      synthesize.py      # M6
      emit.py            # M7
    prompts/
      base.md
      courses/<code>.md
    cli.py               # M8
    mcp_server.py        # M9
  tests/
    fixtures/
  testlecture/               # Benchmark B: real 64-min lecture + both transcripts
```

---

## 5. Library conventions

The school folder already exists and is flat, not per-lecture:

```
~/School/Fall-2026/COSC-4V88/
  slides/Week1.pdf
  Week1.md                   # my own notes — INPUT
  Week1-lecture-notes.md     # generated — OUTPUT
  audio/Week1.m4a
```

**Do not force a migration.** Explicit paths are the primitive; the folder
convention is sugar on top:

```
notes build --audio ~/School/Fall-2026/COSC-4V88/audio/Week1.m4a \
            --deck  ~/School/Fall-2026/COSC-4V88/slides/Week1.pdf \
            --notes ~/School/Fall-2026/COSC-4V88/Week1.md \
            --out   ~/School/Fall-2026/COSC-4V88/Week1-lecture-notes.md
```

A `course.toml` at the course root supplies the code, display name, instructor,
and an optional prompt override, plus a `pattern` (e.g. `Week{n}`) that lets
`notes build COSC-4V88 1` resolve all four paths.

**The output filename must never equal the input notes filename.** `Week1.md` in,
`Week1-lecture-notes.md` out. The pipeline must not be able to eat its own input.

Cache blobs go in `.cache/` at the course root, keyed by content hash — never
beside the notes, where they would clutter the folder the user actually reads.

---

## 6. Output contract

```markdown
---
course: COSC 4V88 — Ethics in AI
lecture: 1
title: Introduction to AI Ethics (Part 1)
instructor: Dr. Blessing Ogbuokiri
date: 2026-09-10
source_audio: Week1.m4a
source_deck: slides/Week1.pdf
duration: 63:54
transcript_engine: whisper large-v3-turbo
generated: 2026-09-10T10:00:04
model: claude-opus-5
---

## TL;DR
Three to five sentences. What this lecture was actually about.

## Exam signals
- Things the prof flagged as important, testable, or "you'll see this again" [12:41]

## Notes

### Slide 4 — Deadweight Loss
**On the slide:** <condensed slide content>
**What the prof added:** <ONLY what is not on the slide> [14:02]
**Worked example:** <if one was given>

## Gaps in my notes
Where my own notes missed or garbled something the prof said.

## Glossary
Term — definition as the prof used it.

## Open questions
Things left unresolved, or that I should ask about.

---
### Transcription confidence
Terms recovered from slide context rather than heard cleanly, so I know what to
double-check. Timestamps stay reliable.
```

The **Transcription confidence** footer is not optional. Validated on the Week 1
lecture: whisper rendered *non-maleficence* as "non-male free science",
*generative AI* as "genetic AI", *ELIZA* as "ELISA", and *UNESCO recommendation*
as "useful recommendations". All four were recoverable from the deck — but the
reader has to be told which claims were reconstructed and which were heard.

### The three rules

These go in the prompt verbatim and are the acceptance criteria for M6.

- **A.** "What the prof added" excludes anything already on the slide. If the
  prof only read the slide aloud, that field is omitted entirely.
- **B.** Every non-obvious claim carries a `[MM:SS]` timestamp so I can jump
  back to the audio and hear it. This is why the transcriber must return
  timestamped segments and never flat prose.
- **C.** Empty sections stay empty. Never invent exam signals. A lecture with no
  flagged material gets an empty "Exam signals" section, and that is correct
  output, not a failure.
- **D.** Mark uncertainty rather than smoothing it. An anecdote is labelled an
  anecdote; a question the class left unresolved goes to Open questions instead
  of receiving an invented answer; a garbled term goes in the confidence footer.

**Rules A, C and D were validated end-to-end on the Week 1 lecture** (deck +
64 min audio + student notes). See `testlecture/` and
`~/School/Fall-2026/COSC-4V88/Week1-lecture-notes.md` for the reference output —
use it as the target when tuning prompts in M6.

---

## 7. Modules

Each is one session. Do them in order; the dependency graph is linear except
where noted.

---

### M0 — Skeleton and config ✅ DONE
**Depends on:** nothing
**Creates:** `pyproject.toml`, `notes_pipeline/{__init__,config}.py`

Python 3.13 in a venv, `pip install -e .` against a `pyproject.toml`.
Dependencies: `anthropic`, `python-pptx`, `pypdf` (fallback only — see M3),
`fastmcp`, `pydantic`, `typer`.

```python
class Config(BaseSettings):
    anthropic_api_key: SecretStr          # env ANTHROPIC_API_KEY
    model: str = "claude-opus-5"
    library_root: Path
    db_path: Path
    transcriber: Literal["whisper", "remote"] = "whisper"
    whisper_bin: Path = Path("whisper-cli")
    whisper_model: Path                    # ggml-large-v3-turbo.bin
    whisper_threads: int = 8
    remote_url: str | None = None
```

Loaded from env, overridden by `~/.config/notes-pipeline/config.toml`.

Neither `anthropic` nor an API key is present on this machine yet — first
session starts with `pip install anthropic` and
`export ANTHROPIC_API_KEY=...` (or `ant auth login`, which the SDK picks up
with no env var).

**Acceptance:** `notes --help` works from the activated venv. A missing API key
produces one clear sentence, not a traceback.

---

### M1 — Domain models ✅ DONE
**Depends on:** M0
**Creates:** `notes_pipeline/models.py`

Pydantic models — the shared vocabulary every later module imports.

```python
class Segment(BaseModel):
    start: float; end: float; text: str
    confidence: float | None = None

class Transcript(BaseModel):
    segments: list[Segment]; duration: float; engine: str; language: str
    def to_timestamped_text(self) -> str: ...     # "[MM:SS] text" per line

class Slide(BaseModel):
    index: int; title: str | None; body: str; notes: str | None

class Deck(BaseModel):
    slides: list[Slide]; source: Path
    def vocabulary(self, limit: int = 120) -> list[str]: ...

class LectureInputs(BaseModel):
    audio: Path; deck: Path | None; my_notes: Path | None; extras: list[Path]

class Lecture(BaseModel):
    id: str; course: str; number: int; title: str | None
    date: date | None; dir: Path
```

**Acceptance:** every model round-trips through JSON. `to_timestamped_text()`
emits `[MM:SS]`, zero-padded, wrapping correctly past an hour.

---

### M2 — Store and content-hash cache ✅ DONE
**Depends on:** M1
**Creates:** `notes_pipeline/{store,cache}.py`

SQLite for metadata; **stage outputs as files on disk** under the lecture's
`.cache/`, not as blobs in the DB. Transcripts are large and you will want to
read them by hand while debugging a bad note file.

```sql
CREATE TABLE lectures(id TEXT PRIMARY KEY, course TEXT, number INT,
                      title TEXT, date TEXT, dir TEXT, created_at TEXT);
CREATE TABLE stage_cache(key TEXT PRIMARY KEY, stage TEXT, lecture_id TEXT,
                         payload_path TEXT, meta TEXT, created_at TEXT);
CREATE TABLE notes(lecture_id TEXT, path TEXT, generated_at TEXT,
                   model TEXT, prompt_version TEXT);
```

```python
def cache_key(stage: str, version: str, inputs: list[Path], params: dict) -> str
def get(key: str) -> Path | None
def put(key: str, data: bytes | str) -> Path
```

Key is `sha256(stage + stage_version + sorted(params) + hashes of inputs)`.
Each stage declares **only its own** inputs.

**Acceptance:** running a stage twice does zero work the second time. Changing
one byte of the deck busts the slides stage and the synthesis stage but **not**
the transcription stage. That last one is the whole point — verify it explicitly.

---

### M3 — Slide extraction ✅ DONE
**Depends on:** M1
**Creates:** `notes_pipeline/stages/slides.py`

```python
def extract(path: Path) -> Deck        # dispatches on .pptx / .pdf
```

`.pptx` via `python-pptx`: iterate slides, pull the title placeholder, body
text frames, and `slide.notes_slide` speaker notes.
`.pdf` via **`pdftotext -layout`** (poppler), not `pypdf`. Verified on the Week 1
deck: lecture slides are two- and three-column, and `-layout` preserves the
column structure so a slide's body text and its sidebar box stay separable.
Naive extraction interleaves them into nonsense. Requires `brew install poppler`;
fall back to `pypdf` only if poppler is unavailable, and warn when you do.

Title heuristic = first non-empty line of the page; the running header
(`COSC 4V88 • Ethics in AI    Brock University`) and the trailing slide number
repeat on every page — strip both before parsing.

`Deck.vocabulary()` returns proper nouns, acronyms, and capitalised multi-word
terms, deduped, frequency-ranked, capped. **Its consumer is M6, not M5** — under
`-mc 0` whisper cannot accept a vocabulary prompt (see M5), so the deck does its
jargon-correction work at synthesis time instead.

**Gotchas:** `python-pptx` returns shapes in XML order, not visual order — sort
by `(top, left)` or the body text comes out scrambled. Empty title placeholders
are common; fall back to the first line of body text.

**Acceptance:** a real deck yields slides in the right order with speaker notes
attached. `vocabulary()` surfaces course jargon and excludes stopwords.

---

### M4 — Audio preparation ✅ DONE
**Depends on:** M1
**Creates:** `notes_pipeline/stages/audio.py`

```python
def prepare(src: Path, dst: Path) -> float    # returns duration in seconds
```

```
ffmpeg -i IN -af loudnorm=I=-16:TP=-1.5:LRA=11 \
       -ar 16000 -ac 1 -c:a pcm_s16le OUT.wav
```

16 kHz mono PCM is what whisper.cpp requires and what `AVAudioFile` reads
without complaint. `loudnorm` matters for quiet lecture-hall recordings.

**Acceptance:** `.m4a`, `.mp3`, `.wav` all produce a canonical wav whose
duration matches the source within 0.1 s. A missing `ffmpeg` gives one clear
sentence.

---

### M5 — Transcription ✅ DONE
**Depends on:** M1, M4
**Creates:** `notes_pipeline/stages/transcribe.py`

```python
class Transcriber(Protocol):
    def transcribe(self, wav: Path, *, vocabulary: list[str] | None = None) -> Transcript: ...

class WhisperTranscriber(Transcriber): ...   # default
class RemoteTranscriber(Transcriber): ...    # POST to a transcription service
def get_transcriber(cfg: Config) -> Transcriber
```

#### whisper.cpp (default)

```
whisper-cli -m MODEL -f WAV -l en -t 8 -np -mc 0 -oj -of OUTBASE [--prompt VOCAB]
```

Parse `OUTBASE.json` → `transcription[].offsets` (milliseconds).

- **Use `large-v3-turbo`, not `large-v3`.** See §2.
- **`-mc 0` guards against the repetition loop.** It disables text-context
  carry-over between windows — the documented trigger for the loop that
  destroyed the `large-v3` run.
- **`-mc 0` and `--prompt` are mutually exclusive.** Verified on Benchmark B:
  runs with `--prompt` (75 words of deck vocabulary) and with
  `--prompt --carry-initial-prompt` were both **byte-identical** to the
  unseeded run. Max-context zero means zero prompt tokens reach the decoder,
  so vocabulary seeding is silently a no-op. Do not ship code that sets both
  and assumes seeding is working.
- **Resolved: `turbo` needs `-mc 0` too, permanently — not just as a fallback.**
  Ran the experiment: `turbo` at default `-mc` with `--prompt` (deck
  vocabulary), against Benchmark B. Result was worse than the `-mc 0`
  baseline, not better — a 377-segment repetition loop (the same failure
  mode `-mc 0` exists to prevent, so it is not `large-v3`-specific after
  all), and jargon recall *dropped* even outside the loop (UNESCO 2→0,
  accountability 4→1, beneficence 3→1). `-mc 0` stays on unconditionally.
  Vocabulary seeding via `--prompt` is dead; jargon correction is entirely
  M6's job (Claude reconciling deck vocabulary against the raw transcript),
  which is already how `synthesize.py` works. See
  `testlecture/whisper_seeded.json` / `whisper_carry.json` for the run
  artifacts, and the comment in `stages/transcribe.py`'s
  `WhisperTranscriber.transcribe` for the inline record.

#### Quality guard (both engines)

Raise a `TranscriptQualityWarning` on any of:
- more than 20 consecutive identical segments (the `large-v3` failure mode);
- a single word repeated 4+ times in a row (whisper scored 0 of these on
  Benchmark B, so any occurrence is a signal the audio is degrading);
- mean confidence below threshold, where the engine reports it.

Catch a bad transcript here, not by reading a hallucinated note file three
weeks later.

**Acceptance:** a 60-minute lecture wav produces monotonic, non-overlapping
segments covering >94% of the duration. Vocabulary seeding demonstrably fixes at least
one jargon term. A synthetic looping transcript trips the quality guard.

---

### M6 — Synthesis ✅ DONE
**Depends on:** M1, M3, M5
**Creates:** `notes_pipeline/stages/synthesize.py`, `prompts/base.md`, `prompts/courses/`

```python
def synthesize(deck: Deck | None, transcript: Transcript,
               my_notes: str | None, extras: list[str],
               course: str, cfg: Config) -> str      # returns markdown
```

Request shape:

```python
with client.messages.stream(
    model=cfg.model,                                  # claude-opus-5
    max_tokens=32000,
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},
    system=[{"type": "text", "text": base_prompt + course_override,
             "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
    messages=[{"role": "user", "content": [
        {"type": "text", "text": deck_block,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": transcript_block,
         "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "text", "text": my_notes_block},     # volatile — after the breakpoint
        {"type": "text", "text": instruction_block},
    ]}],
) as stream:
    message = stream.get_final_message()
```

- **The deck is the jargon corrector.** Whisper will mangle domain terms the
  deck spells correctly (verified: "non-male free science" → *non-maleficence*).
  Deck and transcript must go into the *same* call so Claude can reconcile them,
  and the prompt must say so explicitly. This is why no separate spell-correction
  stage exists.
- Stream — a 32k-token response will otherwise hit the HTTP timeout.
- Cache the deck and transcript with a **1 h TTL**: prompt iteration is the
  main workflow, and a cache read is 0.1× the cost of a fresh read.
- **No assistant prefill.** It returns a 400 on Opus 5. Shape the output with
  the system prompt.
- Verify caching works: `message.usage.cache_read_input_tokens > 0` on the
  second identical run. If it is always zero, something volatile leaked into
  the prefix.
- Consider adding server-side refusal fallbacks
  (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`).
  Vanishingly unlikely to fire on lecture material — drop it if it adds noise.

**Acceptance:** produces markdown containing every required §6 heading. Rule A
holds — spot-check that "What the prof added" contains nothing already on the
slide. Rule C holds — a lecture with no flagged material yields an empty
"Exam signals" section rather than invented ones. Second identical run reports
a cache read.

---

### M7 — Emit ✅ DONE
**Depends on:** M1, M6
**Creates:** `notes_pipeline/stages/emit.py`

```python
def emit(markdown: str, lecture: Lecture, meta: dict, dst: Path) -> Path
```

Validate the required headings are present (warn, don't fail — a partial note
file still beats none). Prepend YAML frontmatter per §6. If `out/notes.md`
exists, move it to `out/.history/notes-<timestamp>.md` before writing.

**Acceptance:** valid parseable frontmatter; stable path; regeneration never
silently destroys a previous version.

---

### M8 — CLI ✅ DONE
**Depends on:** M0–M7
**Creates:** `notes_pipeline/cli.py`

```
notes build --audio A --deck D --notes N --out O [--force] [--no-cache]
notes build <course> <n>            # resolves paths via course.toml `pattern`
notes transcribe <audio>            # transcript only
notes ls [course]
```

Explicit paths are the primitive and must work standalone — the school folder is
flat and will not always match a pattern.

Print one line per stage with cache status and elapsed time:

```
slides      12 slides                     0.3s
audio       2694.3s → wav                 4.1s
transcribe  732 segments (whisper)      374.2s
synthesize  claude-opus-5                 —  cached
emit        out/notes.md
```

**Acceptance:** end-to-end on a real lecture folder. Second run reports every
stage cached. `--force` re-runs everything.

**Added beyond this spec, post-M8, on request (real recurring needs, not
speculative):**

```
notes build ... --notes N.pdf            # --notes now also accepts PDF (extracted via M3's pdf_to_text)
notes build ... --assets A.pdf B.md ...  # supplementary files (any of .md/.pdf/.txt) as extra
                                          # synthesis context — wires up the `extras` param that
                                          # synthesize() already had in M6 but M8 never exposed
notes append --audio P2 --note N.md [--deck D] [--notes N2] [--assets ...] [--out O]
                                          # merges a continuation recording (a lecture split across
                                          # multiple audio files) into an already-generated note.
                                          # Reads the existing note back in as context instead of
                                          # re-transcribing earlier parts. New prompt fragment at
                                          # prompts/append.md handles timestamp disambiguation
                                          # ([Part 2, MM:SS] vs [MM:SS], since the two recordings
                                          # don't share a timeline).
```

---

### M9 — MCP server ✅ DONE
**Depends on:** M8
**Creates:** `notes_pipeline/mcp_server.py`, `notes_pipeline/pipeline.py`, `notes_pipeline/library.py`

FastMCP, stdio transport. Registered with
`claude mcp add notes -- <repo>/.venv/bin/notes-mcp`.

Use the venv's absolute interpreter path — Claude Code spawns the stdio server
as a subprocess without an activated venv, so a bare `notes-mcp` will not
resolve.

**Design constraint that shapes this module:** a full build takes minutes, which
is too long for a synchronous tool call. So builds are jobs.

```python
@mcp.tool() def list_courses() -> list[dict]
@mcp.tool() def list_lectures(course: str | None = None) -> list[dict]
@mcp.tool() def build_lecture(course: str, number: int, force: bool = False) -> dict   # -> {"job_id": ...}
@mcp.tool() def job_status(job_id: str) -> dict                                # -> stage, progress, result path
@mcp.tool() def get_notes(course: str, number: int) -> str                     # the markdown
@mcp.tool() def search_notes(query: str, course: str | None = None) -> list[dict]
```

**Deviation from the original spec above:** `build_lecture`/`get_notes` take
`(course, number)`, not a `lecture_dir` string. The library layout (§5) is
flat and course-root based, keyed by `course.toml` + a lecture number — there
is no per-lecture directory to point at. `(course, number)` is exactly what
`notes build <course> <n>` already resolves on the CLI side, so both entry
points share one resolver (`library.py`) instead of inventing a second path
convention.

`build_lecture` starts a background job (a plain thread — the server is a
single long-lived subprocess of one Claude Code session, so no job queue is
needed) and returns immediately; Claude Code polls `job_status`. `get_notes`
is the one that gets used constantly — it is how "synthesise these then quiz
me on the output" actually works. `list_lectures` reports every lecture number
discoverable from `audio/` under a course's `pattern`, each flagged
`built: bool`, not just the ones already in the store — so Claude Code can see
what's buildable, not only what's built.

To avoid the build logic drifting between the CLI and the MCP server, the
stage-by-stage pipeline (slides → audio → transcribe → synthesize → emit,
previously inlined in `cli.py`'s `_run_build`) was extracted into
`pipeline.run_build()`, parameterized by a `Reporter` protocol. `cli.py`
supplies a `TerminalReporter` (identical output to before — verified against
the cached COSC-4V88 Week 1 run); `mcp_server.py` supplies a `JobReporter`
that updates the `Job` a background thread is running, for `job_status` to
read. Course/path resolution (`course.toml` lookup, `Week{n}`-pattern
matching) was similarly extracted from `cli.py` into `library.py`.

**Acceptance:** driven end-to-end — verified directly (not just imported):
`tools/list` over the real stdio JSON-RPC transport returns all six tools;
`build_lecture` → `job_status` polled to `"done"` → `get_notes` → `search_notes`
all exercised against the cached COSC-4V88 Week 1 lecture. Registered with
`claude mcp add notes -- <repo>/.venv/bin/notes-mcp` and confirmed `✔ Connected`
via `claude mcp list`. Not yet driven from *inside* a live Claude Code chat
turn (say "synthesise this, then quiz me") — do that as a first real use.

---

## 8. Build order

```
M0 → M1 → M2 → ┬→ M3 ┬→ M5 → M6 → M7 → M8 → M9
               └→ M4 ┘
```

M3 and M4 are independent and can be done in either order.

---

## 9. Cost

Per 50-minute lecture, roughly: transcript ~13k tokens, deck ~3.5k,
my notes ~1.5k, prompt ~1.5k → **~19k input**. Output with thinking, ~10k.

At `claude-opus-5` ($5/MTok in, $25/MTok out): **~$0.35 per lecture.**
Five courses × two lectures × twelve weeks ≈ 120 lectures ≈ **$42 a term.**

Prompt iteration on an already-transcribed lecture hits the cache: input drops
to roughly $0.01, so the marginal cost of re-running synthesis to tune a prompt
is the output tokens alone.

Transcription is free.

---

## 10. Deferred

- **FastAPI + Next.js frontend.** Superseded by a full design — see §14. Not
  yet built.
- **Remote transcription** against the 9060 XT box for the *personal Mac
  pipeline* (M0–M9), independent of the hosted multi-user service in §14 —
  `notes build` on the Mac calling out to the box's whisper instead of running
  it locally. `RemoteTranscriber` is the seam (M5). Worth revisiting once §14's
  `api` service exists, since it will already have whisper behind an HTTP
  endpoint on that box for the hosted pipeline — a minimal transcribe-only
  route for personal use may be nearly free to add on top. Vulkan
  `-DGGML_VULKAN=1` on gfx1200 should cut the ~6 min Mac transcription time to
  roughly 2 min. **If built, it must return timestamped segments as JSON —
  not markdown, not flat text.** Rule B depends on timestamps and they cannot
  be recovered once discarded.
- **Obsidian vault integration**, after the vault refactor.

---

## 11. Known risks

| Risk | Mitigation |
|---|---|
| Lecture hall audio is much harder than clean speech | Confirmed — §2 Benchmark B. Default is whisper for this reason |
| A different lecturer or room is worse still | Re-run Benchmark B per course. Note `-mc 0` and vocabulary seeding cannot be used together — see M5 |
| Slide-to-audio alignment is approximate — the prof jumps around | Don't attempt hard alignment; give Claude the whole transcript and let it attribute. This is what the 1M context is for |
| Prof reads the slides verbatim → "What the prof added" is empty | Correct behaviour, not a bug. Rule A |
| A bad transcript silently produces a confident, wrong note file | Quality guard in M5; Rule B timestamps and the Rule D confidence footer make claims checkable |
| **Recording started after the lecture began** | Observed on Week 1 — the audio opens mid-sentence and the first minutes are simply gone. No software fix. `notes build` should warn when the first segment begins at 00:00 with no leading silence, since that is the signature of a late start |
| Deck and audio drift apart (prof skips or reorders slides) | Don't hard-align. Give Claude both in one call and let it attribute — this is what the 1M context is for |
| 6 min of transcription per lecture on the Mac | Acceptable in the background; the 9060 XT box would cut it to ~2 min |

---

## 12. Prerequisites

Already installed and verified on this machine:

| Tool | Path | Needed by |
|---|---|---|
| `ffmpeg` | `/opt/homebrew/bin/ffmpeg` | M4 |
| `pdftotext` (poppler) | `/opt/homebrew/bin/pdftotext` | M3 |
| `whisper-cli` | `/opt/homebrew/bin/whisper-cli` | M5 |
| whisper model | `models/ggml-large-v3-turbo.bin` (1.6 GB) | M5 |

Still missing — do these first:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e .                     # once pyproject.toml exists (M0)
pip install anthropic                # SDK not installed
export ANTHROPIC_API_KEY=...         # or: ant auth login
```

`python3.13` is already present at `/opt/homebrew/bin/python3.13`. Use it
explicitly — the default `python3` is 3.14.6, which is ahead of some wheels.

`models/` and `testlecture/` hold 1.7 GB between them. Add both to
`.gitignore` before the first commit.

Already in the repo and working:

- `testlecture/` — Benchmark B corpus: the real 64-min lecture, normalized wav,
  and the whisper transcripts (plain / seeded / carry-prompt)
- `~/School/Fall-2026/COSC-4V88/Week1-lecture-notes.md` — reference output

---

## 13. Starting a module session

> Read `PLAN.md`. Build module **M<n>**, nothing else. The acceptance criteria
> in that module's section are the definition of done — implement against them
> and verify each one before you finish. Do not modify other modules' files;
> if an interface in `models.py` is wrong, say so rather than working around it.

---

## 14. Hosted multi-user service (planned)

Everything in §§1–13 is the personal, single-user, Mac-local pipeline and is
**done and unaffected by this section.** This is a second, separate surface:
a handful of named friends, each with their own Anthropic key, using a web
dropbox and/or a remote MCP connector, hosted on the 9060 XT box (Ubuntu +
Tailscale) — which is also a gaming PC, so it's off sometimes. Public TLS,
DNS, and routing live one layer out, on the always-on **goosenest02** k3s
cluster, which already has cert-manager and DNS management set up — see
14.1/14.2. Designed in conversation before any of it was built — not yet
implemented. Treat this section the way §§0–9 were treated before they were
built: a spec to implement one module at a time, not a description of
working code.

### 14.1 Decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Where the pipeline runs | The 9060 XT box, in Docker Compose | Frees the pipeline from the Mac; one shared GPU for whisper across all hosted users |
| Where the public edge runs | goosenest02's k3s cluster (ingress-nginx + cert-manager, already set up), **not** the gaming box | The gaming box is a gaming PC first — it's off sometimes. goosenest02 is always on, and already owns cert/DNS management, so reuse it rather than duplicating TLS setup on a second machine |
| How the edge reaches the pipeline | Over Tailscale only — the gaming box publishes **zero public ports**, not even conditionally | Strictly better than the original single-box design: the box most worth protecting now has no public listener at all, on top of the fallback UX benefit |
| When the box is off | ingress-nginx's built-in custom-error-backend serves a static "the pipeline's host is off — text me" page on 502/503/504 | Native ingress-nginx feature for exactly this; no bespoke proxy/health-check code, no wake button (explicitly not wanted — human-in-the-loop by design) |
| Exposure | Public internet, not tailnet-only | Friends shouldn't need to install Tailscale to use a website |
| Auth | Google OAuth via Auth.js (web) and FastMCP's `GoogleProvider` (MCP) | Zero identity infra to run; friends already have Google accounts; "OSS" is the auth library, not the IdP |
| Access control | Explicit per-email allowlist, not open signup | A handful of named friends, not the general public |
| Sessions | Database-backed, not JWT | Must be able to instantly revoke a friend's access by disabling their row |
| API keys | Each user supplies their own Anthropic key; encrypted at rest, never round-tripped to the browser after saving | Their usage, their bill; the box never becomes a shared-cost liability |
| Ingestion | Web dropbox upload only — **not** an MCP tool | A browser-only friend has no server-side file path to point a tool at, and MCP tool calls aren't shaped for large binary uploads |
| Remote MCP tool surface | Read/search/quiz only: `list_my_lectures`, `get_notes`, `search_notes`, `job_status` | Matches what's actually possible remotely; building happens on the website. No fallback page equivalent exists for this surface — when the box is off, a tool call just fails with a connection error, and that's fine |
| Admin | A second instance of the same web app, bound to the gaming box's loopback interface only | SSO-gated *and* network-unreachable except via SSH tunnel — two independent layers, not one. Untouched by the goosenest02/k3s refactor — never routed through it |
| DB | SQLite, shared Docker volume, WAL mode | Matches the existing single-user pipeline's storage decision (§1); still bought nothing to switch to Postgres at this scale |
| Concurrency | One global build worker across all users | One GPU — concurrent whisper runs would just contend with each other, not go faster |

### 14.2 Architecture

Two machines now. **goosenest02** (always-on, k3s) is the only thing with a
public IP in this picture; the gaming box is reachable from it only over
Tailscale, and has no public listener at all — not even conditionally on
being "up."

```
  Public internet
        │
        ▼
  goosenest02 — k3s cluster (ingress-nginx + cert-manager, already exists —
                              this project is just another Ingress on it)
        │
        ├─ Ingress: notes.<domain>  →  ExternalName/Tailscale-reachable
        │            /               →  backend pointing at the gaming
        │            /mcp            →  box's stable Tailscale hostname
        │                              (e.g. gamingbox.tailXXXX.ts.net)
        │
        └─ ingress-nginx custom-http-errors (502/503/504) → a tiny always-on
           in-cluster Deployment+Service serving one static page: "the
           pipeline's host is off right now — text me and I'll turn it
           back on." No wake button, no health-check-triggered automation
           — deliberately just a message, so it's still you who decides
           when the box comes back on.

           No Tailscale operator or subnet router is installed on this
           cluster — Tailscale runs on the goosenest02 host only, which is
           itself a tailnet member. Whether the ingress-nginx pod can reach
           the gaming box's tailnet IP directly (plain pod egress through
           the node) or needs a host-level forwarder instead (mirroring how
           Postgres/`tailscale serve` already works on this box) is settled
           in M11 §14.5, not guessed at here.

  ══════════════════════════ Tailscale (private, WireGuard) ═════════════════

  9060 XT box — Docker Compose, ZERO published public ports
        │
        ├─ web (Next.js) ──────────┐  bound to the box's Tailscale
        ├─ api (FastAPI+FastMCP)   │  interface only — reachable from
        │    mounted at /mcp       │  goosenest02 over the tailnet,
        │    GoogleProvider +      │  from nowhere else
        │    email-allowlist       │
        │    TokenVerifier;        │
        │    single build-worker   │
        │    queue; whisper-cli    │  web → api's /api/* stays exactly
        │    (GPU passthrough)     │  as before: Docker-internal network
        │                          │  on the gaming box, never touches
        │                          │  goosenest02 or the public internet
        ▼                          ▼
  auth.db (Node-only:        notes.db (shared): lectures, stage_cache,
  sessions, accounts —       notes (+ user_id), and the one hand-defined
  Auth.js's own schema)      `users` table both web and api read/write
                             directly (email, disabled,
                             encrypted_anthropic_key, is_admin)

  admin — same image as `web`, different entrypoint/env. Compose publishes
  it as 127.0.0.1:8090:3000 — bound to the gaming box's own loopback
  interface only, never on its Tailscale interface, never routed through
  goosenest02. Reached only via `ssh -L 8090:localhost:8090 you@box`, then
  a fresh Google sign-in (separate origin ⇒ separate session cookie)
  checked against ADMIN_EMAIL.
```

Both Docker volumes (`auth.db`'s and `notes.db`'s) plus uploaded audio and
the whisper model live in named Compose volumes on the gaming box, so a
container crash or `docker compose down` doesn't lose data.

### 14.3 Data model delta

```sql
-- notes.db: existing lectures/stage_cache/notes tables (§ M2) gain a
-- user_id column, scoping every row to whoever uploaded it.
ALTER TABLE lectures ADD COLUMN user_id TEXT;
ALTER TABLE notes    ADD COLUMN user_id TEXT;

-- notes.db: one new table, intentionally tiny and hand-written (no ORM,
-- no migration framework) on EITHER side — this is the one place Python
-- and Node touch the same rows, and the schema needs to stay stable
-- precisely because two languages depend on it.
CREATE TABLE users(
    email TEXT PRIMARY KEY,
    disabled INTEGER NOT NULL DEFAULT 0,
    is_admin INTEGER NOT NULL DEFAULT 0,
    encrypted_anthropic_key TEXT,          -- AES-256-GCM ciphertext, base64
    key_nonce TEXT,                        -- base64, one per key
    created_at TEXT NOT NULL
);
```

Both the web app's Auth.js `signIn` callback and the `api` service's MCP
token verifier ask the same question against this one table: does a
non-disabled row exist for this Google-verified email? Encrypting/decrypting
`encrypted_anthropic_key` is Python's job only (it already owns AES-GCM
logic nowhere else) — the web app's "paste your API key" settings page
calls `POST /api/me/anthropic-key` on the `api` service rather than writing
ciphertext into `notes.db` itself, so the encryption implementation exists
in exactly one place.

Web-app lectures don't use the course.toml/`pattern` convention from §5 at
all — that's specific to the Mac-local flat-folder library. An uploaded
lecture is just `{id: uuid, user_id, title (user-entered, defaults to the
filename), created_at}`; `pipeline.run_build` still works unmodified, just
called with synthetic `course_code`/`number` values instead of ones resolved
from a course.toml.

**Required change to `pipeline.py`:** `Config.anthropic_api_key` is
currently a single value loaded once at process start (fine for one
person's CLI). `run_build` needs an optional per-call key override so each
hosted build uses *that user's* key, not a global one baked into `Config`.
Small, mechanical — not yet done.

### 14.4 Repo layout delta

```
notes-pipeline/
  notes_pipeline/            # unchanged (§4) — M0-M9, personal pipeline
    webapi.py                 # M13 — FastAPI + FastMCP, multi-tenant
  web/                        # M14/M15 — Next.js; `web` and `admin` are two
                               # running instances of this one codebase
  deploy/
    docker-compose.yml        # M10 — gaming box: web, admin, api
    k8s/                       # M11 — goosenest02: Ingress, the
                               # Tailscale-reachable backend, the fallback
                               # Deployment+Service, ingress-nginx
                               # custom-http-errors config
```

### 14.5 Modules

---

#### M10 — Gaming-box Docker Compose skeleton
**Depends on:** M9
**Creates:** `deploy/docker-compose.yml`

Three services (`web`, `admin`, `api` — no `caddy`; TLS/routing now lives on
goosenest02, see M11) per §14.2. Named volumes for `notes.db`, `auth.db`,
uploaded audio, and the whisper model. GPU device passthrough to `api`
(`/dev/dri`, `/dev/kfd`, `render` group membership) for the Vulkan
whisper.cpp build. Every container runs as a non-root user with
capabilities dropped and resource limits set. `web` and `api` bind to the
box's **Tailscale interface only** — reachable from goosenest02 over the
tailnet, from nowhere else, and never published to `0.0.0.0`. `admin`
publishes to `127.0.0.1` only. The host forwards **no public ports at all**;
SSH stays Tailscale-only.

**Acceptance:** `docker compose up` brings up all three services; `api` can
invoke `whisper-cli` with working GPU access; from any machine off the
tailnet, nothing on the box is reachable, full stop; from goosenest02, `web`
and `api` are reachable over Tailscale; `admin` is reachable from neither —
loopback only.

---

#### M11 — goosenest02: ingress + off-box fallback
**Depends on:** M10
**Creates:** `deploy/k8s/` — an `Ingress`, the Tailscale-reachable backend
for `web`/`api`, and the fallback `Deployment`+`Service`

Reuses the cluster's existing ingress-nginx + cert-manager setup — this is
just another `Ingress` on it, with TLS handled the way every other app on
that cluster already gets it. Two routes: `notes.<domain>/` → the gaming
box's `web` port, `notes.<domain>/mcp` → its `api` port. The backend target
is the box's stable Tailscale hostname, not a raw IP (Tailscale IPs are
stable too, but the hostname survives re-registration).

The fallback is ingress-nginx's built-in `custom-http-errors` +
`default-backend-service` mechanism — when the box is off, connecting to it
fails and nginx returns 502/503/504, which this feature intercepts and
serves from a small always-on in-cluster Deployment instead: one static
HTML page, "the pipeline's host is off right now — text me and I'll turn it
back on," nothing dynamic, no wake button (deliberately not built — see
§14.1: this stays a human-in-the-loop step, not automated). This is a
native ingress-nginx feature built for exactly this case, not bespoke
health-check code.

**How the Ingress backend actually reaches the gaming box's tailnet IP —
confirmed constraints, from the goosenest02 cluster's real config (not the
Tailscale Kubernetes operator, not a subnet router; neither is installed):**
Tailscale runs on the goosenest02 **host**, not in-cluster — the node itself
is a tailnet member (`100.97.74.58`). The only mechanism actually in use
today is inbound: klipper/NodePort exposing a pod's port on the node's IPs
(including its tailnet IP), the same pattern the Postgres/`tailscale serve`
setup uses for a host-bound process. Neither of those is quite what this
module needs — they make something reachable *from* the tailnet; this needs
an ingress-nginx *pod* to reach *out* to a different tailnet peer (the
gaming box) as a proxy backend. That's a different direction your existing
setup doesn't directly answer. Two candidates, in the order to actually try
them when this module is built:

1. **Plain pod egress.** k3s's default CNI typically masquerades
   pod-originated traffic for any destination outside the pod/service CIDR
   through the node — which would let it ride the node's already-existing
   route to `100.64.0.0/10` via `tailscale0` for free, no operator, no
   NodePort, no `--advertise-routes`. Verify with one command before relying
   on it: `kubectl exec` into any pod and curl the gaming box's tailnet IP.
   If it connects, the Ingress backend is just an `ExternalName` Service
   pointing at the gaming box's Tailscale hostname — nothing else to build.
2. **Fallback, mirroring the existing Postgres pattern** if (1) doesn't
   work: a small forwarder run directly on the goosenest02 **host** (not a
   pod) — the host already has full tailnet peer connectivity, same as it
   does for Postgres, no extra config needed there — and a Service with
   manually-specified `Endpoints` pointing at the node's IP, which is the
   standard way to route a k8s Ingress to something running outside the pod
   network on the same host without needing any pod-to-tailnet reachability
   at all.

**Acceptance:** with the gaming box up, `notes.<domain>/` and `/mcp` proxy
through correctly over TLS; with the gaming box powered off, the same URLs
return the static fallback page instead of a raw gateway-timeout error;
`admin` is not reachable through this Ingress at all — it was never wired
into it.

---

#### M12 — Shared `users` table and key encryption
**Depends on:** M2, M10
**Creates:** additions to `notes_pipeline/store.py` (the `users` table +
`ALTER TABLE ... ADD COLUMN user_id`), a small encryption helper module

AES-256-GCM helpers (`encrypt_key(plaintext, master_key) -> (ciphertext,
nonce)`, `decrypt_key(ciphertext, nonce, master_key) -> plaintext`), master
key from an env var, never logged, never returned by any API response.

**Acceptance:** round-trips through encrypt/decrypt; a disabled user's row
is excluded from an "is this email active" query; schema matches §14.3
exactly, since M13 and M14 both depend on it verbatim.

---

#### M13 — `api` service: multi-tenant FastAPI + remote MCP
**Depends on:** M9, M12
**Creates:** `notes_pipeline/webapi.py`

```python
# internal only — never reaches goosenest02's Ingress or the public internet
POST /api/lectures                 # multipart upload -> queues a build job
GET  /api/lectures                 # this user's lecture/job history
GET  /api/lectures/{id}/notes      # download the generated markdown
GET  /api/jobs/{id}
POST /api/me/anthropic-key         # encrypt + store this user's key

# mounted at /mcp — the only path of this service goosenest02's Ingress proxies publicly
@mcp.tool() def list_my_lectures() -> list[dict]
@mcp.tool() def get_notes(lecture_id: str) -> str
@mcp.tool() def search_notes(query: str) -> list[dict]
@mcp.tool() def job_status(job_id: str) -> dict
```

`FastMCP(auth=GoogleProvider(...))`, with a custom `TokenVerifier` subclass
wrapping Google's that additionally rejects any token whose verified
`claims["email"]` isn't an active row in `users` — confirmed feasible
directly against the installed `fastmcp` 4.0.3 source before committing to
this design. `http_app()`'s `allowed_hosts`/`host_origin_protection` locked
to the real domain, as a DNS-rebinding guard. One global build-worker queue
processes uploads FIFO across all users, with a small per-user pending-job
cap so one person can't monopolize the only GPU.

Every tool/route resolves the calling user from their verified session/token
— never trusts a client-supplied user id.

**Acceptance:** an upload completes a full build and is downloadable; a
request to any `/api/*` or `/mcp` route without valid auth is rejected; a
non-allowlisted Google account is rejected during the MCP OAuth flow itself,
not after; `tools/list` over real HTTP returns exactly the four read tools.

---

#### M14 — `web`: dropbox + job history
**Depends on:** M13
**Creates:** `web/` (Next.js, Auth.js)

Auth.js with `GoogleProvider`, database session strategy, a `signIn`
callback querying the shared `users` table. Three pages: upload (streams
multipart straight through to `api`'s `/api/lectures`, no buffering a whole
audio file in the Next.js process), job history (poll `job_status`, list
past lectures, download button hitting `/api/lectures/{id}/notes`), and a
settings page to paste in an Anthropic key (posts to `api`, never stores
ciphertext itself).

**Acceptance:** an allowlisted Google account can sign in, upload, watch
progress, and download; a non-allowlisted account is rejected with a clear
message; disabling a user (M15) kills their live session within one request.

---

#### M15 — `admin`: loopback-only user management
**Depends on:** M14, M12
**Creates:** an `admin` entrypoint on the same `web` codebase; the `admin`
Compose service

Same Next.js build as `web`, gated additionally on
`session.user.email === env.ADMIN_EMAIL`. One page: list users (email,
created_at, disabled, has-a-key?), add-by-email, and a disable/re-enable
toggle. "Remove" is soft-disable, not delete — keeps a departed friend's
lecture history intact rather than orphaning rows; a hard-delete can be
added later if actually wanted.

**Acceptance:** unreachable via `curl` from any host other than the box
itself; reachable via `ssh -L 8090:localhost:8090`; requires its own Google
sign-in matching `ADMIN_EMAIL`; adding a user lets them sign in on `web`
moments later; disabling one ends their session immediately.

---

#### M16 — Hardening pass
**Depends on:** M10–M15
**Creates:** nothing new — a verification pass against the checklist below

- The gaming box forwards **no public ports, period** — not 80/443, not
  anything. Confirmed by scanning it from off the tailnet. SSH reachable
  only over Tailscale.
- Tailscale ACLs restrict which tailnet nodes can reach the gaming box's
  `web`/`api` ports to goosenest02 specifically, not every device on the
  tailnet.
- `unattended-upgrades` (or equivalent) enabled on the gaming box's host OS.
- Every gaming-box container: non-root user, dropped capabilities,
  memory/CPU limits.
- Secrets (Google client secret, the AES master key, `ADMIN_EMAIL`) live in
  an `.env` the Compose stack reads, `chmod 600`, never committed —
  `.gitignore` updated before this module is considered done.
- goosenest02's Ingress sends standard security headers (HSTS, etc.) — same
  bar as every other app already on that cluster.
- MCP `allowed_hosts` genuinely locked to the production domain, not `*`.
- Single build-worker queue verified under two simultaneous uploads from
  different users — second one waits, doesn't contend for the GPU.
- Per-user pending-job cap verified — a burst of uploads from one account
  gets throttled, not queued unbounded.

**Acceptance:** every item above independently verified, not just present
in config.
