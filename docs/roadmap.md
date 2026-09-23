# Roadmap

Queued tasks, most-ready first. Each says what, why, where, and done-when. Anything
that touches filling must keep the golden rules in `CLAUDE.md` (human gates, no
auto-submit, no runtime model calls).

---

## Now — quick wins

> Items 1, 2 and 3 are all done, a desktop app now wraps the engine
> (`docs/webapp.md`), and the Clerk form's document table is filled from an imported
> record list (below). The "Now" section is clear; the next real work is item 4
> (more counties) and the hardening items called out below.

### 1. Verify the DoS form's law-enforcement & judges boxes — **DONE (2026-09-21)**
Both verified, plus `military` re-confirmed. Each mark was rendered and the label
beside it read; all three land on the correct box. Recorded in
`docs/verification-checklist.md`. Original task below, kept as the method.

<details><summary>original</summary>

- **Why:** `military` was visually confirmed on the DoS form; LE and judges were placed
  by the same coordinate method but not eyeballed. Cheap insurance before a real filing.
- **Where:** generate with a temp client per category, render pages 1–2, confirm the
  right square is marked.
  ```bash
  sed 's/category: "military"/category: "judges"/' clients/example_client.yaml > clients/_tmp.yaml
  python fill_forms.py --client clients/_tmp.yaml --forms fl_dos_public_records_exemption
  pdftoppm -png -r 100 output/doe_john/fl_dos_public_records_exemption__primary.pdf /tmp/chk
  # repeat for law_enforcement; then rm clients/_tmp.yaml
  ```
- **Done when:** both categories mark the correct box; note it in `docs/verification-checklist.md`.
</details>

### 2. `--all-persons` flag on fill_forms.py — **DONE**
- Shipped as `fill_forms.fill_client(client, persons=[...], form_keys=[...])` with a
  `--all-persons` CLI flag. `python fill_forms.py --client clients/example_client.yaml
  --all-persons` yields both the primary and spouse sets in one run. The web app's
  generate screen uses the same function to fill for several people at once.

### 3. `validate_client.py` pre-flight check — **DONE**
- Shipped as one `validate_client(client, form_keys=None)` used by both the CLI
  (`python validate_client.py --client ... | --all | --strict`, non-zero exit on
  errors) and the web app, which renders the same issues inline on the edit form.
- Warnings are generated from the mappings themselves — it reads each selected
  mapping's `from:` keys and reports any that resolve empty — so a new mapping is
  covered with no validation code.

### Import the Clerk's document list — **DONE (2026-09-22)**
The Clerk redaction form's document table used to be left blank for the courthouse
visit. A client's `documents:` list now fills it: `import_documents.py` (or the
Documents screen) reads a CSV/paste from an Official Records search, and an overlay
mapping's `tables:` block stamps a row per ruled line. Past the form's four lines it
prints "Continued on Attachment A" and appends the whole list for initialling.
Still human: running the search and deciding which records belong on the request.

Hillsborough's search has an **Export to Spreadsheet** button (verified 22 Sep 2026;
click path in the README), and its real headings — `Instrument, RecordDate, DocType,
BookNum, PageNum` — are read directly, `BookType` deliberately ignored. No scraper was
needed and none was written.

Open follow-ups, if the worker asks for them:
- Other counties' redaction forms get their own `tables:` block; see
  `docs/adding-forms.md`. Check each county's export headings against
  `documents.ALIASES` — adding a spelling there is a one-line change.
- ~~A row-picker on the Documents screen~~ — **done (2026-09-22)**: the import lists
  every parsed row with a tick box, the party names and a filter; the CLI got
  `--match`. Party names are shown to choose by and never stored.

---

## Hardening (added 2026-09-21, ranked by what would hurt most)

### H1. The worklist overwrites tracked work — **DONE (2026-09-23)**
Rebuilding now merges: Status / Submitted / Confirmation / Verify-by / Notes are read
back out of the existing sheet and carried across, matched on a hidden Key column
(site name for sheets built before it). Rows for sites that have left the catalog are
kept and marked, and the previous file is copied to `_worklist_backups/` first. The
catalog's own text moved to a separate "Site guidance" column so merging is not
ambiguous about whose note is whose.

### H2. No backup or export — **DONE (2026-09-23)**
`case_export.py` packages a case — the client file, every generated file, the
generation log — into one zip with a manifest hashing each member. `--all` backs up
every client, `--verify` re-checks an archive before you rely on it, `--restore` puts
one back (refusing a damaged archive, an existing client without `--replace`, and any
member whose path points outside the installation). The app has an **Export case**
button on the filled-forms screen; it builds in memory so no second plaintext copy is
left behind.

Client files are also versioned locally in `clients/_backups/<slug>/` on every save.

Still true: an export is **not encrypted** — Python's zipfile cannot write one, and
adding a dependency for it would be a bigger promise than this can keep. The README,
the in-app notice and the archive's own README all say to keep exports on an encrypted
volume. If that stops being good enough, `pyzipper` is the route.

### H3. Flatten output + log generations (was #9) — **half done (2026-09-23)**
Logging is in: `generation_log.py` writes one line per filled form into
`output/<client>/.generation-log.jsonl` — the form, the person, SHA-256 of the output,
of the mapping and of the blank PDF, plus the library versions that drew it. The
filled-forms screen flags a file that no longer matches what was generated.

Still open: filled values are live text, not flattened, so a generated PDF can be
edited after the fact. The log now makes that detectable; flattening would make it
hard.

### H4. No automated tests — **DONE (2026-09-23)**
236 tests under `tests/`, ~11s, run with `pytest`. They cover context building, the
overlay round-trip on a synthetic form, validation, output naming, the document
parser, the worklist merge and `check_mapping` over every shipped mapping — plus a
golden test that pins each value's coordinate on the real Clerk form (it skips
without the gitignored blank PDFs, so CI runs the rest). The suite redirects
`paths.ROOT` to a temp dir before importing anything, so it exercises the real save
and delete paths without going near real client data. See `docs/testing.md`.

The web app is covered too (2026-09-23): the security gate check by check, the
form-data → client-file layer, create/edit/delete/generate/worklist through the real
app, the document import and row picker, and hand corrections with both drift guards.
374 tests in all.

The mapping editor, the form importer and the Comment feature are covered too
(2026-09-23). 458 tests in all. Writing those found three live bugs — a 500 on every
refused mapping save, an editor edit that deleted the Clerk form's `tables:` block,
and colliding comment filenames — all fixed in the same commit.

### H5. `trash/` is never pruned — **DONE (2026-09-23)**
`housekeeping.py` reports what has piled up in `trash/` and `feedback/` (screenshots
of the app are client data too) and prunes past a retention — 90 days for trash,
30 for feedback. It only ever runs when a person types it: nothing on a schedule,
nothing at startup, because "the tool quietly deleted the case I was restoring" is
worse than a folder that grew.

### H6. No one-page guide for the worker
The README is written for a developer.

## Next — real capability

### 4. Add more Florida counties
- **Why:** the client isn't Hillsborough-only. This is the main growth path and needs
  **no code** — just new mappings.
- **Where:** drop each county's blank forms in `forms/_blank_pdfs/`, follow
  `docs/adding-forms.md`, add `forms/mappings/<county>_*.map.yaml`. Route by
  `home_county` if two counties' forms could both apply.
- **Done when:** a client in the new county fills that county's forms, verified per
  `docs/verification-checklist.md`.

### 5. Wire the Optery data-broker API
- **Why:** replace the prepare-by-hand worklist with actual submissions + status. See
  `docs/decisions.md` D2 (Optery has a real developer API; pricing is custom/contact-sales).
- **Where:** new `broker_removals.py` — submit custom removals for a client, receive
  webhook status, write results next to the worklist. Keep opt-outs the vendor performs
  (their ToS handling), never our own scrapers. **Get the quote first** — API tier is
  negotiated, not the consumer sticker.
- **Done when:** a client's broker exposures are submitted via API and status is
  reconciled back into the worklist/tracker.

### 6. Sunbiz / registered-agent workflow
- **Why:** business filings expose the home address; the fix is a **record change**
  (amend filing + registered-agent/address service), not a removal. Ties into the DoS
  form's Division-of-Corporations addendum.
- **Where:** a short checklist/generator that, given a discovered Sunbiz record, fills
  the DoC addendum with the alternate address and outputs the amendment steps.
- **Done when:** given a client with a Sunbiz record + alternate address, the DoC
  addendum (DoS form p3) is filled and the amendment steps are listed.

---

## Later — scale

### 7. Discovery query generator
- **Why:** today the worker finds exposures manually. A deterministic generator turns a
  client file into the search queries to run (name variants, address, phone, email,
  spouse, business) — no model needed.
- **Where:** new `discover.py` producing a query list / worklist of places to check.
- **Done when:** a client file yields the full set of searches to run, deduped.

### 8. Migrate to the database
- **Why / when:** only when volume justifies it — see `docs/future-scale/README.md` for
  the signals and the schema. The form engines and mappings don't change; only where
  case data is read from.
- **Done when:** cases/persons/exposures/actions live in Postgres and `fill_forms.py`
  reads a case from the DB instead of a YAML, producing identical PDFs.

### 9. Output flattening + audit trail
- **Why:** flattened PDFs read as final (values not editable), and a per-run log of what
  was generated for whom supports the GRC/ISO-42001 story (`docs/decisions.md` D5).
- **Where:** flatten in `pdf_fill.py`/`pdf_overlay.py` on write; append a line to a
  local generation log.
- **Done when:** outputs are flattened and each run records client + forms + timestamp.

---

## Not on the roadmap (by decision, not omission)
- Auto-submitting government forms or clicking through opt-out CAPTCHAs — stays human
  (`CLAUDE.md` rules, `docs/decisions.md` D4).
- Deciding exemption eligibility — stays human.
- In-house data-broker scrapers — buy the engine instead (D2).
