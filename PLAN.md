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

### M4 — Audio preparation
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

### M5 — Transcription
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
- **Unresolved:** whether `turbo` needs `-mc 0` at all. The loop was only ever
  observed on `large-v3`. The experiment to run: `turbo` at default `-mc`
  with `--prompt`, checked against the quality guard. If it holds, seeding
  becomes available and `-mc 0` can be reserved as the fallback for a
  recording that actually loops.
- `--prompt` caps at roughly `n_text_ctx/2` (~224) tokens — truncate.

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

### M6 — Synthesis
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

### M7 — Emit
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

### M8 — CLI
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

---

### M9 — MCP server
**Depends on:** M8
**Creates:** `notes_pipeline/mcp_server.py`

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
@mcp.tool() def build_lecture(lecture_dir: str, force: bool = False) -> dict   # -> {"job_id": ...}
@mcp.tool() def job_status(job_id: str) -> dict                                # -> stage, progress, result path
@mcp.tool() def get_notes(lecture_dir: str) -> str                             # the markdown
@mcp.tool() def search_notes(query: str, course: str | None = None) -> list[dict]
```

`build_lecture` starts a background job and returns immediately; Claude Code
polls `job_status`. `get_notes` is the one that gets used constantly — it is how
"synthesise these then quiz me on the output" actually works.

**Acceptance:** driven end-to-end from a Claude Code session — build a lecture,
poll to completion, pull the notes back, and get a quiz out of it without
touching the terminal.

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

- **FastAPI + Next.js frontend.** `POST /jobs`, `GET /jobs/{id}`,
  SSE `/jobs/{id}/events`. The job model in M9 is designed so this drops in
  without restructuring anything.
- **Remote transcription** against the 9060 XT box once it is running Ubuntu on
  the tailnet. `RemoteTranscriber` is the seam. **This is now worth doing** —
  whisper is the accuracy default and costs ~6 min per lecture on the Mac; a
  Vulkan `-DGGML_VULKAN=1` build on gfx1200 should cut that to roughly 2 min,
  and moves the load off the laptop entirely. **If that service is built, it
  must return timestamped segments as JSON — not markdown, not flat text.**
  Rule B depends on timestamps and they cannot be recovered once discarded.
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
