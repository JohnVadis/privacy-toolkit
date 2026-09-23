# Privacy Removal Toolkit (Florida) — v1

A lean, local toolkit that kills the repetitive part of the client's work: retyping
the same name, address, DOB, and phone into form after form. Enter each client's
info **once**, then generate filled government PDFs and a pre-filled opt-out worklist
in seconds. No database, no subscription, no AI, no per-run token cost.

Two front ends, one engine: a **CLI** for the technical path, and a **desktop app**
for a non-technical worker. Both call the same functions and produce byte-identical
PDFs.

## Project docs
- `CLAUDE.md` — context + rules for Claude Code (read first if you're an agent here).
- `docs/architecture.md` — data flow and context-variable reference.
- `docs/webapp.md` — web UI routes, the reuse boundary, how the rules are enforced.
- `docs/adding-forms.md` — map a new county form (incl. finding checkbox coordinates).
- `docs/forms-reference.md` — field-by-field detail of the four wired forms.
- `docs/verification-checklist.md` — run before any form's first real use.
- `docs/decisions.md` — why it's built this way.
- `docs/roadmap.md` — prioritized next tasks (ready to hand to Claude Code).
- `docs/future-scale/` — the database design to migrate to later (not wired in).

## What it does
1. **Fill government PDF forms** — maps a client's info onto fillable county forms
   (Clerk redaction, Property Appraiser confidentiality, FLHSMV, etc.) and outputs
   ready-to-review PDFs, one per person.
2. **Build an opt-out worklist** — a spreadsheet, pre-filled with the values each
   data-broker / property / search site asks for, plus status-tracking columns.
3. **Run it all from a desktop app** — manage clients and generate forms without
   touching YAML. Nothing is published; it runs entirely on one machine.

## The one idea that makes it fast
Work per *form* is done **once**; work per *client* is **zero**.

- **Once per county form:** run `scaffold_mapping.py` on the blank PDF, then edit the
  generated mapping to point each field at a client variable. ~10 minutes, one time.
- **Every client after that:** `fill_forms.py` reuses that mapping automatically.
  Deterministic — same input, same output, no model in the loop.

## Setup

On Windows, skip this — `Start Privacy Toolkit.bat` does it for you the first time you
open it. Otherwise:
```bash
pip install -r requirements.txt
```

## Giving it to someone else
Two ways, depending on whether they can install Python.

```bash
python build_exe.py --zip        # a Windows app that needs nothing installed (~45 MB zipped)
python make_test_copy.py --zip   # a source copy; needs Python, sets itself up (~1.3 MB zipped)
```

`build_exe.py` produces `dist/Privacy Toolkit/` — an .exe they double-click, with
`clients/`, `output/`, `forms/` and `sites.yaml` beside it as ordinary files they can
open and back up. Nothing to install and no internet needed. Windows shows a
"Windows protected your PC" warning the first time because the app isn't code-signed;
"More info" then "Run anyway" clears it for good.

`make_test_copy.py` is the smaller source copy for someone who has Python, or is
willing to install it. Both build from an allowlist and refuse to finish if any value
from a real client file appears in the output.

## The desktop app (easiest way in)

**First time:** double-click `Start Privacy Toolkit.bat`. It builds a private Python
environment, installs what it needs (the one step that needs internet, ~40 seconds),
puts a **Privacy Toolkit** icon on the Desktop and in the Start menu, then opens the app.

**After that:** double-click the **Privacy Toolkit** icon. It opens straight into the
app in about two seconds — no black console window. Closing the app window shuts
everything down.

Go back to `Start Privacy Toolkit.bat` only if something breaks: it repairs the
installation, recreates the shortcut, and shows any error.

From a terminal instead:
```bash
python -m webapp.desktop                # the app window
python -m webapp.main --open-browser    # browser instead, if you prefer
```

### It runs on one machine, and only for you
The UI is HTML, so a small web server runs behind the window — but it is private by
construction:

- bound to **127.0.0.1 on an OS-assigned port** (nothing fixed to guess),
- requests with a foreign `Host` or a cross-site origin are **refused**, which is what
  stops a web page you have open from quietly driving the app,
- every request needs a **session key** minted fresh each time it starts,
- **closing the window stops the server** — nothing is left running.

Verified by re-running the attacks: foreign `Host`, cross-site form POST, and a local
process without the key are all refused with 403. Details in `docs/webapp.md`.
Open <http://127.0.0.1:8000>. You get a client list, a real form for creating and
editing clients (add people, addresses, phones and emails without editing YAML),
pre-flight validation, and a generate screen with inline page-1 previews and download
links. The worklist screen builds the spreadsheet and shows the site catalog read-only.

It binds to **loopback only** and makes **no external requests** — htmx and the CSS are
vendored locally. `--no-access-log` matters: URLs contain client slugs, so the access
log would be a PII log. Full detail in `docs/webapp.md`.

Anything the UI writes is an ordinary `clients/<slug>.yaml`, so the CLI below keeps
working on it — and vice versa. (One caveat: saving a client from the UI rewrites the
file and drops its YAML comments. That only matters for `example_client.yaml`.)

## Try the built-in demo (works out of the box)
```bash
python fill_forms.py --client clients/example_client.yaml       # -> output/doe_john/*.pdf
python build_worklist.py --client clients/example_client.yaml   # -> output/doe_john/opt_out_worklist.xlsx
```
`clients/example_client.yaml` + `forms/_blank_pdfs/demo_redaction.pdf` +
`forms/mappings/demo_redaction.map.yaml` are a working example. Delete them before real use.

## Real workflow

### 1. Enter a client once
Copy `clients/_TEMPLATE.yaml` to `clients/lastname_firstname.yaml` and fill it in.
Add a `spouse`/`child` block under `persons:` if the engagement covers them — the
same address flows through, so a spouse form is one command, not a re-type.

### 2. Add a county form (once per form)
```bash
python scaffold_mapping.py forms/_blank_pdfs/hillsborough_redaction.pdf fl_clerk_redaction_hillsborough
```
Open the new `forms/mappings/fl_clerk_redaction_hillsborough.map.yaml` and set each
field: `{ from: full_name }`, `{ literal: "s. 119.071(4)(d), F.S." }`, or for
checkboxes `{ checkbox: true, check: true }` / `{ checkbox: true, when: {field: exemption_code, equals: FL_119_071_4d} }`.
Variables available: `full_name first_name middle_name last_name suffix dob phone
email home_line1 home_line2 home_city home_county home_state home_zip home_full
case_number case_display_name agent_name agent_email exemption_code exemption_title
exemption_cite today`.

### 3. Fill forms for a client
```bash
python fill_forms.py --client clients/lastname_firstname.yaml                 # all mapped forms, primary person
python fill_forms.py --client clients/lastname_firstname.yaml --person spouse # same forms, spouse
python fill_forms.py --client clients/lastname_firstname.yaml --all-persons   # every person in one run
python fill_forms.py --client clients/lastname_firstname.yaml --forms fl_clerk_redaction_hillsborough
```

Check a client file first — it catches a missing county, DOB or statutory category
before you generate a form with a blank in it:
```bash
python validate_client.py --client clients/lastname_firstname.yaml   # non-zero exit on errors
python validate_client.py --all                                      # every client file
```
Output lands in `output/<client>/`. Review, **notarize where the county requires it**,
then submit.

### 4. Import the documents to be redacted
The Clerk form names the recorded documents it applies to — instrument number, book,
page, title — and only what it names gets redacted.

Getting the list out of Hillsborough (verified 22 Sep 2026):
1. <https://publicaccess.hillsclerk.com/oripublicaccess/> → search type **Name**.
2. Last/Business Name (+ first name, document types, recording date range if you want
   to narrow it).
3. **Search**, then **Export to Spreadsheet** above the results grid. It writes
   `OfficialRecords_Results.csv` to your Downloads with the headings `Instrument,
   Grantor, Grantee, RecordDate, DocType, BookType, BookNum, PageNum, Legal,
   SalesPrice`.
4. Don't prune it in Excel — import the whole thing and pick the rows. The search
   matches a name, not a person, and deciding which records are this client's is the
   human part.

### Three ways this search will lie to you

All three were hit on a real client on 22 Sep 2026. Each one silently produces a
SHORT list, and a short list on this form is a defective filing — the Clerk redacts
only what the form names.

**Always add `%` to the name.** The plain search matches the indexed name almost
exactly, and modern records are indexed without the comma the older ones use. Same
surname, same date range:

| Query | Results |
|---|---|
| `MARLOWE` | 482 |
| `MARLOWE%` | 4,387 |
| `MARLOWE`, 1990–2019 only | **0** |
| `MARLOWE%`, 1990–2019 only | **2,919** |

A plain search missed 89% of the index, including every record that actually named the
client. Wildcard both fields: `MARLOWE%` / `PRIYA%`.

**Search every name the client has held.** `MARLOWE` + `PRIYA` returns nothing for a
client whose deed is recorded under her maiden name. Search the maiden name, prior
married names and any spelling variants, and import each search in turn — imports
merge, so the second does not discard the first.

**Check the CSV's row count against the number on screen.** On a 4,387-row result set
the export button re-downloaded a byte-identical copy of the *previous* search's file,
and a second attempt produced no file at all. It is reliable on small sets. Before
importing:

```bash
# the header line means one fewer than the file's line count
wc -l "OfficialRecords_Results.csv"
```
If that doesn't match the "N results" the page reported, narrow the search (by date
range or document type) and export in slices rather than trusting the file.

Then:
```bash
python import_documents.py --client clients/lastname_firstname.yaml records.csv
python import_documents.py --client clients/lastname_firstname.yaml records.csv --dry-run
python import_documents.py --client clients/lastname_firstname.yaml records.csv --match "SMITH PAMELA"
python import_documents.py --client clients/lastname_firstname.yaml releases.csv --list release
```

**Picking the rows.** In the app, the import lists every parsed row with a tick box,
the party names beside it, and a filter — type a name, click *Tick shown*, import. On
the CLI, `--match` keeps only the rows containing the text you give it (repeat it to
narrow further), and the preview prints the parties. Nothing is ticked by default and
nothing is written until you confirm: the form redacts exactly what it names.

The party names are shown so you can choose, then dropped. What lands in the client
file is the record's own identifiers — no third party's name is stored.
That export's headings are read as-is. So are plainer ones (Instrument Number, Book,
Page, Document Title), other counties' spellings (CFN, OR Book, Doc Type, a combined
"Book/Page"), and a straight paste of the results grid, which arrives tab-separated.
Columns it doesn't recognize are listed back to you and ignored. Imports merge, so one
search at a time is fine — the married name, then the maiden name. In the app, the same
thing lives on a client's **Documents** screen.

Recent Hillsborough records are identified by instrument number alone, so `BookNum` and
`PageNum` often come back empty; those columns then print blank on the form, which is
what the Clerk expects.

The form rules four lines for documents; with more than that it prints three and
"Continued on Attachment A", and appends a sheet listing every one for the requestor to
initial. With nothing imported the table prints blank — write it in at the counter.

### 5. Build the opt-out worklist
Edit `sites.yaml` to match the client's checklist, then:
```bash
python build_worklist.py --client clients/lastname_firstname.yaml
```

## Tests
```bash
pip install -r requirements-dev.txt
pytest
```
236 tests, about eleven seconds, and they never touch `clients/` or `output/` — the
suite points the toolkit at a temporary directory first. The one test that fills the
real Clerk form and checks every coordinate skips unless the county blank PDFs are
present. Details in `docs/testing.md`.

## Seeing what's been generated
Every client row shows a **Filled forms** count. Click it for that client's forms —
newest first, with the real form name, who it's for, and when it was made. Each one
offers **View** (read every page in the app, zoom and print), **Save a copy**, and
**Show in folder**.

Because this reads the `output/` folder directly rather than any index, forms filled
from the CLI show up in the app too.

## Fixing something that's wrong
Two ways, depending on who it affects.

**One client's form** — open it and press **Edit text**. The form appears with an
editable box on every value and a real tick box on every checkbox:

- type straight onto the form to change a value,
- click a box to tick or untick it,
- click any empty spot to add text or a tick there.

Save once and it re-generates. Your changes are kept against that client and re-applied
every time, so pressing Generate again won't lose them. Clear a box back to its original
text to undo that change.

**Every client's form** — Forms → **Edit** on a mapping. The blank form sits beside the
file: click the page to get a coordinate, and press **Preview with a client** to see the
filled result before saving. Every save keeps a backup in `forms/mappings/_backups/`,
and a mapping with a problem (bad coordinate, wrong page, typo'd key) is refused rather
than written.

## Adding a new form
**Forms → Add a form**, drop in the blank county PDF. The toolkit reads it and proposes
a mapping, then opens the layout editor.

Every value appears as a **marker** on the form itself. Click one to see what it writes;
drag it, or nudge it with the **arrow keys** (1 pt, or 10 pt with Shift) to land on a
line exactly. **+ Value** and **+ Tick box** add what wasn't detected, the panel changes
what a marker writes without touching YAML, and **Delete** removes one.

The editor **opens already previewed**, filled with a made-up example client, so you can
see straight away whether every value lands on the right line. Each marker is drawn as
the box that value will actually fill. Switch the picker to a real client to check
theirs, and you can keep working on the preview either way. The markers stay put, so when you see a value in the wrong place you drag it right
there and the preview re-draws itself. Every save keeps a backup.

How much it can work out depends on the PDF. If it has real form fields (like the demo
form) everything is found. If it's a flat scan it finds most of the text values but
**none of the checkboxes** — on these county forms the boxes aren't text, so there's
nothing to detect and you place those yourself. If it's a plain image, nothing can be
detected at all. The importer tells you which case you're in.

From a terminal: `python form_import.py <file.pdf> <short_name>` to see the proposal,
`--write` to save it.

## Sending a comment
**Comment** at the top of any screen takes a picture of what you're looking at. Drag on
it to **Highlight** what you mean, or **Hide** anything that shouldn't leave the machine
— a hidden area is blacked out in the picture itself, not just covered over. Write your
note and it opens an email to john@qvadisai.com with the comment filled in.

The picture is saved in `feedback/` and that folder is opened for you, because no email
link can attach a file — drag it in. Nothing is sent until you press send.

## Deleting a client
In the app, **Delete** on the client list (or at the bottom of the edit form) shows a
confirmation page naming the client and listing every generated file that will go.
Nothing is erased: the client file and their `output/` folder are moved into
`trash/<date>__<client>/`, so a misclick is recoverable. Empty `trash/` yourself when
you're sure — that folder is gitignored, like `clients/` and `output/`.

## What stays manual (on purpose)
- **Submitting** to agencies/sites, and **notarization** — these need a person.
- **Eligibility** — confirm the client actually qualifies for the claimed exemption
  before generating anything that asserts it. The tool doesn't judge eligibility.
- **Opt-out sites** use CAPTCHAs and email verification; the worklist prepares the
  values and tracks status, a human does the click-and-submit.
- **Finding the documents** — the toolkit imports a record list, it does not search the
  Clerk for you. A person runs the search and decides which records belong on the
  request; there is no scraper here and no call to any county system.

## Two kinds of form, one command
`fill_forms.py` handles both automatically based on the mapping's `mode:`

- **fillable** — the PDF has real form fields; values are written into them.
- **overlay** — the PDF is a flat scan (no fields); values are stamped on by
  coordinate. Most county forms are flat, so this is the common case. An overlay
  entry locates its value one of two ways:
  - `{ anchor: "Home Address:", dx: 8, dy: 0, from: home_full }` — finds that label
    on the page and stamps just after it (resilient to small form changes).
  - `{ page: 1, x: 172, y: 425, from: home_full }` — an exact coordinate, used when
    the scan has no text layer to anchor to (pure image).
  - Checkboxes use `mark`: `{ page: 1, x: 62, y: 326, mark: "X", when: { field: role, equals: spouse } }`.

To coordinate-map a new flat form, render it to an image (`pdftoppm -png -r 100 form.pdf out`),
read off positions, and add entries. Anchor first; fall back to absolute for image-only scans.

## Forms wired up (Hillsborough County) — all verified
- `hillsborough_appraiser_confidentiality` — Property Appraiser Request of Confidentiality (119.071(4)(d)).
- `hillsborough_appraiser_military` — Property Appraiser Confidentiality – Service Member (119.071(5)); marks member/spouse/dependent by role.
- `hillsborough_clerk_redaction` — Clerk redaction (4 pp): requestor type by role, statutory category by `exemption.category`, contact info, what-to-redact boxes (address/phone/DOB), job title/agency, notary county. Document table (instrument/book/page) left blank — filled at the courthouse.
- `fl_dos_public_records_exemption` — FL Dept. of State exemption (4 pp): attestation by role, category by `exemption.category`, identity fields, notary county. Division of Corporations addendum (p3) left blank unless the client has Sunbiz records.

**Statutory category** is set once per client in `exemption.category` — `military`,
`law_enforcement`, or `judges` — and each form checks the correct box automatically.
Add more categories by extracting the box coordinate for that label and adding a
`when: { field: exemption_category, equals: <name> }` entry to the mapping.

## Security (this holds real PII)
- Keep `clients/` and `output/` **out of version control** and encrypted at rest
  (full-disk encryption or an encrypted volume is enough at this scale).
- The included `.gitignore` excludes them. Never commit a real client file.

## Files
```
Start Privacy Toolkit.bat   double-click once: installs, makes a Desktop icon, opens the app

clients/            one YAML per client (source of truth) + template + example
trash/              deleted clients land here (recoverable); gitignored
forms/_blank_pdfs/  drop blank county forms here
forms/mappings/     one .map.yaml per form (written once, reused forever)
sites.yaml          opt-out site catalog for the worklist
output/             generated PDFs + worklists, per client

paths.py            repo-root anchor, so paths don't depend on the working directory
pdf_fill.py         list/fill fillable PDF fields (pypdf)
pdf_overlay.py      stamp values onto flat scans (pypdf + reportlab + pdfplumber)
client_context.py   load/save a client file; flatten it into template variables
scaffold_mapping.py setup: blank PDF -> starter mapping
fill_forms.py       per-client: fill all mapped forms
documents.py        the client's recorded-document list + the CSV parser that fills it
import_documents.py per-client: import a Clerk record list from a CSV or a paste
pdf_attachment.py   the "Attachment A" sheet when a document list outruns the form
validate_client.py  pre-flight a client file (shared by the CLI and the web app)
mapping_edit.py     check/back up/save/reposition a mapping; `python mapping_edit.py` checks all
form_import.py      read a blank PDF and propose a mapping for it
pdf_render.py       render a PDF page to PNG (previews, and the click-to-place picker)
build_worklist.py   per-client: pre-filled opt-out worklist (.xlsx)
webapp/             the local web UI (FastAPI + HTMX). Wraps the engine, never duplicates it
```
