# Future scale — the database path (NOT wired in)

This folder is reference for later. The live toolkit does **not** use a database; at
current volume it doesn't need one (see `../decisions.md` D1). Keep it here so the
design isn't lost, and migrate when the signals below appear.

## When to migrate
- More clients/records than one person can track in a spreadsheet.
- Multiple people need shared, live status on the same cases.
- You want automated re-scans, follow-up reminders, and client progress reports.
- You're integrating the data-broker removal API and want status reconciled per record.

## What's here
- `schema.sql` — state-agnostic Postgres 15+ core: cases, persons, addresses,
  identifiers, exemptions (+ per-case claims), sources, exposures, actions, append-only
  `status_events` and `audit_log`, and a deterministic `routing_rules` table with a
  `v_exposure_routing` view.
- `seed_florida.sql` — Florida exemptions catalog, source catalog, and routing rules
  (Clerk redaction, Property Appraiser confidentiality, Sunbiz record-change, FLHSMV,
  property sites, data-broker → removal-engine API, search-engine deindex).

Run order: `psql < schema.sql` then `psql < seed_florida.sql`.

## How it maps to the current toolkit
| Toolkit (now) | Database (later) |
|---|---|
| `clients/<name>.yaml` | `cases` + `persons` + `addresses` + `identifiers` |
| `exemption.*` in the YAML | `exemptions` + `case_exemptions` (with `human_verified` gate) |
| discovered listings / records | `exposures` |
| a filled form / opt-out attempt | `actions` (+ `status_events`) |
| `sites.yaml` | `sources` + `routing_rules` |
| the opt-out worklist spreadsheet | `v_case_dashboard` / `v_followups` views |

The router in `routing_rules` is the DB-side equivalent of "which workflow does this
exposure go to" — still deterministic, still no model. The same human gates
(`case_exemptions.human_verified`, `actions.requires_human_review`) carry over.

## What does NOT change on migration
The form-fill engines (`pdf_fill.py`, `pdf_overlay.py`) and the mapping files are
independent of storage — they'd read case data from the DB instead of a YAML, and keep
producing the same PDFs. Migrating storage doesn't mean rewriting the form logic.
