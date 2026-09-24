# CLAUDE.md — Privacy Removal Toolkit

Operating context for Claude Code working in this repo. Read this first.

## What this is
A local, deterministic toolkit that streamlines a privacy-removal practitioner's
work: enter a client's info **once**, then generate filled Florida government PDF
forms and a pre-filled data-broker opt-out worklist. It replaces the retype-the-same-
info-into-every-form drudgery. No database, no subscription, and **no model calls at
runtime** — every fill is a table lookup + coordinate stamp.

There are two front ends over one engine: the original CLI, and a desktop app
(`webapp/`, HTML in a pywebview window) for non-technical workers. Both call the
same functions.

Current scope: **Hillsborough County, FL**. Four forms wired and verified.

Runs on Windows and macOS. Everything platform-specific goes through one branch —
`paths._root()` (where user data lives), `webapp.deps.reveal()`, `desktop.problem()`
and `feedback._print_window` / `_mac_window`. Add a platform by extending those, not
by scattering `sys.platform` checks; `tests/test_platforms.py` covers the dispatch.

## Golden rules (do not break these)
1. **Eligibility is human-verified.** The tool never decides whether a client
   qualifies for a statutory exemption. It fills forms from data it's given.
2. **Government forms are never auto-submitted.** Output is a filled PDF for a human
   to review, notarize where required, and submit. There is no submission code, and
   none should be added.
3. **Opt-out sites are prepared, not submitted.** They gate on CAPTCHAs and email
   verification; `build_worklist.py` produces values + a status tracker, a human clicks.
4. **PII never gets committed.** `clients/*` (including `_backups/`), `output/`,
   `trash/` and `feedback/` are gitignored. SSN is never stored anywhere (the FL
   redaction workflow doesn't need it). Check `git check-ignore` before adding any
   new folder that touches client data.
5. **Keep it deterministic.** No LLM/API calls in the fill path. If a task seems to
   need judgment (classifying results, reading logs), that's a separate, out-of-band
   step — not part of these scripts.
6. **The web app wraps; it never reimplements.** `webapp/` is a front end. If you're
   about to write a coordinate, a field name or a value lookup under `webapp/`, it
   belongs in a mapping or the engine. It also binds to 127.0.0.1 only, makes no
   external requests, vendors its JS/CSS, and never logs field values.
7. **Don't weaken `webapp/security.py`.** The Host check, the cross-site check and the
   session token are what stop a web page the worker has open — or any other local
   process — from driving the app. Removing any one of them re-opens a hole that was
   demonstrably exploitable. Never bind to anything but loopback.

## Commands
```bash
pip install -r requirements.txt

# Fill all mapped forms for a client (primary person)
python fill_forms.py --client clients/<name>.yaml
python fill_forms.py --client clients/<name>.yaml --person spouse
python fill_forms.py --client clients/<name>.yaml --forms hillsborough_clerk_redaction

# Fill for every person in the file (spouse, children) in one run
python fill_forms.py --client clients/<name>.yaml --all-persons

# Run the tests (see docs/testing.md). The golden test skips without the blank PDFs.
pip install -r requirements-dev.txt
pytest
pytest tests/test_golden_hillsborough.py   # coordinates on the real Clerk form

# Pre-flight a client file before generating (exits non-zero on errors)
python validate_client.py --client clients/<name>.yaml
python validate_client.py --all

# Import the Clerk's Official Records list onto a client (fills the form's document table)
python import_documents.py --client clients/<name>.yaml official_records.csv
python import_documents.py --client clients/<name>.yaml export.csv --match "SMITH PAMELA"
python import_documents.py --client clients/<name>.yaml releases.csv --list release
python import_documents.py --client clients/<name>.yaml export.csv --dry-run

# Build the opt-out worklist spreadsheet (MERGES — never destroys tracked status)
python build_worklist.py --client clients/<name>.yaml

# Package a case into one file (backup, handover, closing record)
python case_export.py --client clients/<name>.yaml
python case_export.py --all --to D:/encrypted-backups
python case_export.py --verify "exports/<file>.zip"
python case_export.py --restore "exports/<file>.zip" [--replace]

# See what client data has piled up in trash/ and feedback/, and prune it
python housekeeping.py                        # report only
python housekeeping.py --prune --dry-run
python housekeeping.py --prune

# Run the desktop app
python -m webapp.desktop                # app window; closing it stops the server
python -m webapp.main --open-browser    # browser instead; prints a URL holding the key
python -m webapp.main --port 8000       # pin the port (default: OS assigns one)
# Non-technical path: double-click the launcher for the platform (installs, then runs)
#   Windows: "Start Privacy Toolkit.bat"     macOS: "Start Privacy Toolkit.command"

# Hand it to someone else
python build_exe.py --zip        # standalone Windows app, no Python needed
python make_test_copy.py --zip   # source copy, needs Python; allowlist + PII scan

# Inspect a new PDF / scaffold a mapping for a fillable form
python pdf_fill.py forms/_blank_pdfs/<form>.pdf          # lists fillable fields
python scaffold_mapping.py forms/_blank_pdfs/<form>.pdf <form_key>
```
Output lands in `output/<client file name>/` — keyed on the client FILE, not the
display name, because two clients can share a display name and then share a folder.

## Architecture (one line each)
- `clients/<name>.yaml` — single source of truth per client (people, addresses, identifiers, exemption).
- `paths.py` — anchors every path to the repo root, so a server process is CWD-independent.
  Frozen into an .exe, `ROOT` is the .exe's folder (user data) and `BUNDLED` is the temp
  unpack dir (templates/static). Never write user data under `BUNDLED` — it is deleted
  on exit.
- `client_context.py` — load/save a client file and flatten it into template variables.
  `save_client()` is atomic (temp + fsync + os.replace), backs the previous version up
  to `clients/_backups/<slug>/`, and takes a lock so the CLI and the app can't
  interleave. `output_dir()` lives here too — one definition, used by everything.
- `forms/mappings/<key>.map.yaml` — one per form, written ONCE, reused for every client. Declares `mode: fillable` or `mode: overlay`.
- `pdf_fill.py` — writes values into AcroForm fields (fillable PDFs).
- `pdf_overlay.py` — stamps values by anchor/coordinate onto flat scans (most county forms).
- `fill_forms.py` — dispatches on `mode`, resolves values, writes filled PDFs.
- `validate_client.py` — one `validate_client()` used by both the CLI and the web app.
- `mapping_edit.py` — check/back up/save a mapping, and `move_entries()` to reposition
  one WITHOUT re-dumping the YAML (that would strip the comments). Never write a mapping
  without `check_mapping()`.
- `form_import.py` — blank PDF -> proposed mapping. Honest about its limits: checkboxes
  on these flat county forms are undetectable, and an image-only scan yields nothing.
- `pdf_render.py` — page -> PNG, and the point size needed to turn a click into a coordinate.
- `documents.py` — the client's `documents:` list (instrument/book/page/title) and the
  CSV parser that fills it. Parses; never fetches. One store, two lists (`redact`,
  `release`), merged on import so a second search doesn't discard the first. Party
  names (`PARTY_FIELDS`) are parsed for the row picker and deliberately NOT stored —
  `normalize()` keeps `FIELDS` only. Don't widen that.
- `pdf_attachment.py` — the continuation sheet. A form rules 4 lines for documents and
  a client has more, so the last line says "Continued on Attachment A" and the
  attachment carries the WHOLE list, initialled by the requestor.
- `tables:` in an overlay mapping — repeats one `documents:` row per ruled line. The
  mapping owns the geometry; how many rows there are is the client's business.
- `client_context.EXAMPLE_CLIENT` — fictional client for previewing a form layout. Never
  written to clients/, never used for a filing.
- Overlay coordinates are the start of the text BASELINE (reportlab `drawString`), not a
  centre point. Anything drawing a marker must anchor left/bottom there.
- `overrides:` on a client — replace what a mapping entry writes, applied DURING the
  fill (stamping over existing text would overlap). Keyed by entry position/field name,
  with `from:` re-checked so a mapping edit can't move one onto another field.
- `marks:` on a client — force a tick box on or off, beating the mapping's own rule.
  Carries `at:` (a `mark_signature()` of where the mark lands) which is re-checked at
  fill time, so reordering a mapping can't move someone's tick to another box.
- `corrections:` on a client — extra overlay entries stamped AFTER the mapping, for
  blanks no entry covers.
- All three live in `model.UNMANAGED_KEYS`; drop one and a client save wipes it.
- `pdf_overlay.entry_position()` is what lets the editor place a control exactly where
  the engine will draw, anchors included. Keep resolve_entries using it.
- `sites.yaml` + `build_worklist.py` — opt-out catalog → per-client tracker (.xlsx).
  Rebuilding MERGES: the worker's Status/Submitted/Confirmation/Notes are read back
  out and carried across, matched on a hidden Key column. Never make it overwrite.
- `webapp/` — FastAPI + HTMX UI in a desktop window. Imports the engine; no fill logic of its own.
- `webapp/security.py` — Host / cross-site / session-token gate in front of every request.
- `webapp/desktop.py` — the pywebview window; server on a background thread. Downloads
  need `ALLOW_DOWNLOADS=True` or pywebview cancels them silently. A startup failure is
  shown in a dialog (MessageBoxW / osascript) because a launched app has no console.
- macOS notes: a frozen build is a `.app`, so `paths._root()` walks OUT of the bundle —
  user data beside it, never inside, where a reinstall would erase it. Window capture
  uses Quartz + `screencapture -l` and finds the window by OWNER PID, never by title —
  macOS reveals titles only to a process that already holds Screen Recording, so a
  title lookup fails on exactly the machines that need it and fails silently, by
  photographing the whole desktop. Permission is preflighted and refused loudly
  (`ScreenPermissionNeeded` -> 503), because without it `screencapture` returns a
  correctly sized picture of the desktop and the feature looks like it worked.
  pyobjc ships with pywebview, but it is imported inside a function, so the frozen
  build must list `Quartz`/`AppKit`/`Foundation`/`objc` in `build_exe.HIDDEN` — that
  omission is what shipped the desktop-photo bug. macOS applies a newly granted
  permission only to a process started afterwards, so the app must be restarted.
- `generation_log.py` — one JSONL line per filled form, in
  `output/<client>/.generation-log.jsonl`: what was produced, from which mapping and
  blank PDF (by SHA-256), with the library versions that drew it. `verify()` answers
  "is this file still the one the toolkit made?". Never raises — a form that
  generated is worth more than its audit line.
- `case_export.py` — a whole case as one zip: the client file, every generated file,
  the generation log, and a manifest with a SHA-256 of each. `verify()` re-hashes an
  archive, `restore()` puts it back and refuses a damaged one or an existing client.
  Member paths are checked on restore — a zip can name a member `../../anything`.
  NOT encrypted: Python's zipfile can't, so the docs say to keep exports on an
  encrypted volume rather than pretending otherwise.
- `housekeeping.py` — retention for the two folders that fill with client data as a
  side effect (`trash/`, `feedback/`). Reports by default; prunes only when asked,
  never on a schedule and never at startup.
- `trash/` — deleted clients (file + generated output) are MOVED here, never unlinked.
- `feedback.py` + `webapp/routers/feedback.py` — the Comment feature. It captures the
  window, lets the user redact, saves to `feedback/` (gitignored) and OPENS a mail
  client. It never sends. This is the only path by which client data can leave the
  machine, so keep the redact tool, the warning and the human send step.

Data flow: **client YAML → context vars → mapping → engine → filled PDF**. The mapping
is the only per-form work; everything else is generic.

## The core principle
Work per **form** happens once (map its fields/coordinates). Work per **client** is
zero. When adding a county or form, you're writing one mapping file, not code.

## Key conventions
- Python 3, standard library + pypdf / pdfplumber / reportlab / openpyxl / PyYAML.
  Versions are PINNED EXACTLY in requirements.txt: reportlab decides where a glyph
  sits and a minor release can move text on a filed form. Bumping one means re-running
  `pytest` (the golden test checks real coordinates) and docs/verification-checklist.md.
- Dates render MM/DD/YYYY regardless of YAML formatting (see `client_context._fmt_date`).
- Statutory category is set once per client in `exemption.category`
  (`military` | `law_enforcement` | `judges`); each form checks the right box via a
  `when: { field: exemption_category, equals: ... }` mapping entry.
- Checkbox marks in overlay mode use `mark: "X"` + a `when`/`check` condition.

## Where to look
- `docs/architecture.md` — full data flow + context-variable reference.
- `docs/webapp.md` — web UI routes, the reuse boundary, and how the rules are enforced.
- `docs/adding-forms.md` — how to map a new county form (incl. finding checkbox coords).
- `docs/forms-reference.md` — field-by-field detail of the four wired forms.
- `docs/macos.md` — running it on a Mac: Gatekeeper, Python, the two permissions,
  and what to check on a first run (nothing has been run on a Mac yet).
- `docs/testing.md` — what the suite covers, and how it stays off real client data.
- `docs/verification-checklist.md` — run this the first time any form is used for real.
- `docs/decisions.md` — why it's built this way (buy-vs-build, overlay, human gates).
- `docs/roadmap.md` — prioritized next tasks, most-ready first.
- `docs/future-scale/` — the database design to migrate to IF volume grows. Not wired in.

## Definition of done for a new form
A mapping is done when a rendered fill (`pdftoppm -png` the output) shows every value
on its line and every checkbox in its box, verified against a real client's data and
spot-checked for each statutory category the form will be used for.

Then pin it: `tests/test_golden_hillsborough.py` is the pattern — assert each value's
coordinate on the county's own PDF, and assert the boxes that must stay EMPTY are
empty. That is what turns "someone checked it once" into a test that fails the next
time a coordinate moves.
