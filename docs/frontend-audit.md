# treehouse frontend audit

Full pass over `web/` (Next.js 16 + Auth.js + React 19) — what's there today,
the gaps, and a prioritized list of what to build. Ordered to match what was
asked for first, then widens out into everything else worth fixing.

---

## Where things stand today

Covers the bare mechanics — Google sign-in, an upload form, a lecture list,
an API-key settings form, an admin user table — with almost no visual design
pass: default form controls, a single flat `.page-container`, no loading
skeletons, no empty-state polish, no toasts, no drag-and-drop. It works, but
every screen looks like a first draft.

---

## Priority 1 — upload progress bar + pipeline progress bar

**Current state:**
- `upload-form.tsx` posts via `fetch()` with a `FormData` body and only
  tracks a boolean `submitting` — no bytes-sent feedback at all during the
  (possibly multi-hundred-MB) audio upload.
- `lectures-view.tsx` polls `/api/jobs/[id]` every 2s and renders the job
  snapshot as a single line of text: `Job xxxxxxxx: running — transcribe
  (running whisper on 3834s of audio...) — 0%`.

**Why a literal 0–100% bar is the wrong shape:** the backend's `progress`
field (`notes_pipeline/webapi.py` `JobReporter`) is `None` through most of a
stage and is only set to a real fraction during `transcribe_progress()` (via
whisper) and pinned to `1.0` at `emit_done()`. There is no continuous
percentage across the whole pipeline — the real signal is **stage
position**. The pipeline stages, in order, are:

```
slides (optional) → audio → transcribe → synthesize → emit
```

**What to build:**
1. **Upload progress bar** — `fetch()` cannot report upload progress
   reliably; switch the upload to `XMLHttpRequest` (or a
   `ReadableStream`-backed fetch body with manual chunking if you want to
   stay fetch-only) and drive a determinate `<progress>`/bar off
   `xhr.upload.onprogress` (`event.loaded / event.total`). This covers the
   "audio file is huge, browser is chugging" phase before a job even exists.
2. **Pipeline progress bar** — once `job_id` comes back, render a 5-step
   stage tracker (skip "slides" visually if no deck was uploaded) with the
   current stage highlighted/animated and prior stages checked off. Inside
   the **transcribe** step specifically, use the real `progress` fraction
   for a sub-bar (it's the only stage that reports one, and it's also
   the slowest — §2 of `PLAN.md` clocks it at ~6 min/lecture on the Mac).
   Other stages just show a "step N of 5, in progress" spinner state, since
   there's no fractional signal for them.
3. Surface `job.message` (e.g. "3 slides", "63:54 -> wav") next to the
   current step as it updates — it's already sent, just not styled.
4. On `status === "error"`, keep the job card visible with the stage it
   failed on highlighted red, not just a generic error paragraph.

---

## Priority 2 — upload UX: cleaner buttons + drag-and-drop

**Current state:** three raw `<input type="file">` elements with plain text
labels ("Audio (required)", "Slide deck (optional, .pptx/.pdf)", "Your
notes (optional, .md/.pdf/.txt)"). No drag-and-drop, no filename/size
confirmation once picked, no per-field clear button.

**What to build:**
- Replace each raw file input with a styled dropzone component: a
  bordered/dashed box with an icon + "drop a file here or click to browse"
  that swaps to a compact "chosen file" chip (name, size, an ✕ to clear)
  once a file is selected.
- Wire `onDragOver`/`onDragEnter` (call `preventDefault()`, toggle an
  `is-dragging` class for a highlight) and `onDrop` (`event.dataTransfer.
  files`) on each dropzone — this is what makes "download slides in Chrome,
  drag the tab's download straight in" actually work, since Chrome lets you
  drag a download-shelf/downloads-page entry as a file object.
- Keep the underlying `<input type="file">` for click-to-browse and for
  keyboard/screen-reader access — the dropzone should be a styled wrapper
  around it, not a replacement that breaks a11y.
- Validate extension/type client-side on both drop and pick (`.pptx`/`.pdf`
  for deck, `.md`/`.pdf`/`.txt` for notes) and show an inline error instead
  of letting a bad file reach the server only to 400 back.
- One shared dropzone component parameterized by label/accept/required,
  used for all three fields — don't hand-roll three copies.

---

## Priority 3 — login page centering

**Current state:** `login/page.tsx` renders a bare `<div>` with `<h1>`, an
optional error `<p>`, and a sign-in `<form>` — no layout styling at all, so
it just sits at the top-left inside `.page-container` (`max-width: 720px`,
horizontally centered, but the login content itself isn't vertically or
visually centered within the viewport).

**What to build:** a dedicated `.login-page` wrapper (min-height 100vh minus
header, flex, centered both axes) around a card (border, radius, padding,
subtle shadow) containing the title, error message, and the Google
sign-in button — styled as a real button (Google's own button guidelines,
or at minimum an icon + "Sign in with Google" with proper padding/weight)
rather than the default unstyled `<button>`.

---

## Priority 4 — header layout: settings next to logout on the right

**Current state:** `layout.tsx`'s `.site-header` is `justify-content:
space-between` with nav links (`Upload`, `Lectures`, `Settings`) on the left
and the sign-out form (email + "Sign out") on the right — so "Settings" is
already grouped with the other nav links, not next to logout.

**What to build:** pull `Settings` out of the left-hand `<nav>` and into the
right-hand cluster, ordered `Settings · user-email · Sign out` (or as an
icon-only gear button before the sign-out control). Left nav becomes just
`Upload` / `Lectures`. This is a small JSX move in `layout.tsx` plus
possibly turning the right cluster into a proper flex group so Settings
doesn't visually collide with the sign-out `<form>`'s own flex layout.

---

## Lectures list — feature gaps

- No delete or retry action — a failed or unwanted lecture just sits there
  forever (or requires DB surgery). Add a delete button per row (with a
  confirm step) and a "retry" action for `status === "error"` jobs once the
  backend supports re-queuing.
- No per-lecture detail view — clicking a row does nothing; there's no way
  to see full job history, re-download slides/notes inputs, or preview the
  generated markdown without leaving the app. A `/lectures/[id]` page would
  cover this.
- No in-app markdown preview — "Download" is the only way to see notes;
  rendering the markdown inline (even a collapsed preview) would save a
  round trip through a file manager.
- No search/filter/sort — fine at 5 lectures, not at 50. Add a text filter
  by title and a course/date sort once there's enough volume to matter.
- No empty state polish — "No lectures yet." is a single unstyled line;
  pair it with a CTA button straight to `/upload`.
- Job tracking is single-job, query-param-only (`?job=`) — refresh the page
  or upload a second lecture in another tab and you lose track of the first
  job. Worth moving to a small in-progress-jobs list (poll all non-terminal
  jobs for the user, not just the one in the URL) once concurrent uploads
  are common.

---

## Settings page gaps

- No indication of whether a key is *already* saved — the form always
  shows a blank password field, so there's no way to tell "I have a key on
  file" from "I've never set one." Show a masked state (`sk-ant-••••1234`
  or just a "Key on file" badge) when `has_key` is true.
- No way to remove/clear a saved key without overwriting it with a new one.
- Settings page has exactly one setting. Natural home for anything else
  that becomes per-user config later (default course, notification prefs,
  etc.) — no action needed now beyond keeping the page structured for it.

---

## Admin page gaps

- `toggleDisabled` has no confirmation — one misclick disables a friend's
  account instantly. Add a confirm step (native `confirm()` is banned per
  the browser-automation dialog rules elsewhere in this stack, but a plain
  React confirm modal is fine and is the right call here regardless).
- No way to remove a user outright, only disable — fine if that's
  intentional (audit trail), but worth confirming it's a deliberate choice
  and not just an unimplemented action.
- No search/pagination — same "fine at 5, not at 50" issue as the lecture
  list, lower priority since this is a small allowlist by design.
- `new Date(u.created_at).toLocaleDateString()` — no loading/error state if
  `created_at` is malformed; low risk, not worth touching unless it
  actually breaks.

---

## Cross-cutting UX

- **Loading states:** every async view (`lectures`, `admin`) shows a bare
  "Loading…" string, no skeleton rows. Cheap to add a 2–3 row skeleton
  matching the table shape.
- **Error handling:** errors render as a single red `<p className=
  "error-message">` at the top of a form — fine for now, but there's no
  toast/snackbar system, so success states (e.g. "API key saved") are
  static, easy-to-miss text rather than a transient confirmation. Worth a
  shared toast component once there are more mutating actions (delete,
  retry, admin actions) that all need the same "did that work?" feedback.
- **Responsive/mobile:** `.page-container` and `.site-header` have no media
  queries — nav + user-email + sign-out will wrap awkwardly on a phone.
  Worth a real pass once the desktop layout is settled (hamburger nav or a
  stacked header below ~480px).
- **Accessibility:** no `aria-live` region for job-status polling updates
  (a screen reader won't announce stage changes), no visible focus states
  beyond browser defaults, file inputs have proper `<label htmlFor>`
  already (good) — keep that pattern when building the dropzone component.
- **Design system consistency:** `globals.css` is a reasonable start
  (CSS custom properties, dark-mode media query, `.field`/`.badge`/
  `.error-message` utility classes) but there's no component library or
  shared button/card/input primitives — every page hand-rolls its own
  inline styles (`users-table.tsx` even has a literal `style={{
  marginBottom: "1.5rem" }}`). Worth consolidating into a small set of
  reusable classes (`.card`, `.btn`, `.btn-secondary`, `.stack`) as the
  progress-bar and dropzone work touches most pages anyway.
- **Dark mode:** handled via `prefers-color-scheme` only — no manual
  toggle. Not a gap unless you specifically want an override control.

---

## Feature ideas beyond polish

- **Notifications when a job finishes** — right now you have to keep the
  tab open and watch the poll. A `Notification` API ping (with permission
  prompt) when a job hits `done`/`error` would let you tab away during the
  ~6-minute transcribe stage.
- **Job history** — the job snapshot disappears once you navigate away;
  there's no persisted view of "what happened to my last 5 uploads,"
  especially failures. Ties into the lecture-list gaps above.
- **Multi-file audio** — noted in memory as a known backend/API limit (one
  audio file per lecture), not a frontend bug. Flagging here only so it
  isn't rediscovered as a "missing feature" during this UI pass — it's a
  backend constraint, out of scope for `web/` changes.
- **Keyboard shortcuts** — e.g. `g` then `u`/`l`/`s` to jump between
  Upload/Lectures/Settings, `Escape` to clear a dropzone. Nice-to-have,
  low priority.

---

## Recommended build order

1. Upload progress bar (XHR-based) + pipeline stage tracker — highest
   value, directly requested, and the stage-tracker component is reusable
   scaffolding for the job-history feature later.
2. Dropzone component (drag-and-drop + cleaner buttons) for all three
   upload fields, sharing one component.
3. Login page centering + styled sign-in button — small, isolated, quick
   win.
4. Header reshuffle (Settings next to Sign out) — trivial JSX move, bundle
   with #3 since both touch layout chrome.
5. Lectures list: delete/retry actions, empty-state CTA, per-lecture detail
   page.
6. Settings: show "key on file" state + remove-key action.
7. Cross-cutting pass: shared button/card primitives, toast system,
   responsive header — do this once items 1–4 have settled the visual
   language, so the shared primitives are extracted from real usage
   rather than guessed up front.
