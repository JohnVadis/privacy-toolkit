# Key decisions

Short record of why the toolkit is built the way it is, so future changes don't
undo the reasoning.

## D1 — Deterministic scripts + spreadsheet, not a platform
At current volume (a handful of clients) a per-client YAML plus a status spreadsheet
beats a database-backed app: nothing to host, no per-run cost, trivial to hand off. The
full case-management schema still exists in `docs/future-scale/` for when volume
justifies it. Signals to migrate: more clients than one person can track in a sheet,
multiple people needing shared live status, or wanting automated re-scans/reporting.

## D2 — Buy the data-broker removal engine; don't build scrapers
Broker/people-search opt-out is a commoditized, brittle treadmill (4,000+ brokers,
constantly changing forms, CAPTCHAs, ToS friction). Independent testing put the best
automated services near ~68% removal vs ~35% average and ~70% for careful manual work,
so in-house scrapers are high-maintenance for mediocre results. Plan: integrate an
existing engine via API (Optery has a real developer API — custom removals, webhooks,
exposure metadata; API/embed pricing is custom/contact-sales, not the consumer sticker).
Steer away from vendors with financial ties to data brokers.

**Until that's integrated**, the broker layer is `build_worklist.py` — a pre-filled,
status-tracked worklist a human works through. Opt-out sites are prepared, not
auto-submitted.

Related: the FL Division of Corporations (Sunbiz) exposes home addresses via business
filings. The fix is a **record change** (amend the filing + use a registered-agent /
business-address service), not a removal — which is why the DoS form's DoC addendum
asks for an alternate address.

## D3 — Overlay engine for flat scans
All four Hillsborough forms are flat scans with zero AcroForm fields, so the AcroForm
path (`pdf_fill.py`) doesn't touch them. `pdf_overlay.py` stamps values by anchor
(text-layer forms) or absolute coordinate (image-only scans). `stamp()` clones the
source and overlays only pages with entries, so image-heavy multi-page scans keep their
content (a naive page-merge blanks them).

## D4 — Human gates are non-negotiable
Eligibility determination, notarization, signing, and submission stay with a person.
The tool prepares paperwork; it does not decide who qualifies or file anything. This is
both correct (statutory judgment) and safer (a wrong auto-filing affects a real record).

## D5 — PII handling
`clients/` and `output/` are gitignored; real client files never get committed. SSN is
never stored — the FL redaction workflow explicitly says do not list it, and the forms
redact address/phone/DOB/photo instead. Keep the working copy on an encrypted volume.
This posture also supports the GRC/ISO-42001 story: least-data-stored, clear audit of
what was generated.

## D6 — Statutory category as a single client field
`exemption.category` (`military` | `law_enforcement` | `judges`) is set once per client
and each form checks the correct box via a `when` condition. Adding a category is one
coordinate + one mapping line per form, not a code change — keeps the checkbox matrices
maintainable without hardcoding 40 boxes.
