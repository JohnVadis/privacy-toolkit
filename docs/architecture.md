# Architecture

## Data flow
```
clients/<name>.yaml         one file per client — the single source of truth
        │
        ▼
client_context.build_context(client, person)
        │   flattens the chosen person into template variables
        ▼
forms/mappings/<key>.map.yaml   ← written once per form, reused for every client
        │   says which variable/literal/checkbox goes where
        ▼
fill_forms.py  ──►  pdf_fill.py     (mode: fillable — AcroForm fields)
               └─►  pdf_overlay.py  (mode: overlay  — flat scans)
        │                 ▲
        │                 │  repeating rows from the client's `documents:` list
        │            documents.py ──► pdf_attachment.py  (the overflow sheet)
        ▼
output/<client_slug>/<form_key>__<person>.pdf
```
Nothing in this path calls a model. Same input → same output.

## Two front ends, one engine
```
   CLI (fill_forms.py --client ...)      web UI (webapp/, FastAPI + HTMX)
                    └───────────┬────────────────┘
                                ▼
              fill_forms.fill_client() / fill_one()
              build_worklist.build_worklist()
              client_context.load_client() / build_context() / save_client()
              validate_client.validate_client()
```
The engine functions are the boundary. The CLI entry points live under
`if __name__ == "__main__"` and call them; the web app imports and calls the same
ones. No logic is duplicated, so the two produce byte-identical output — that's
verifiable by md5-ing `output/<slug>/` after each. See `docs/webapp.md`.

Paths are anchored to the repo root by `paths.py` rather than the working directory,
because a server process's CWD is not guaranteed.

## Progress reporting
`fill_one()` and the two PDF engines take a `report` callback (default `print`). The
CLI prints; the web app passes a collector so progress lines — which name the client
and the output paths — are rendered once in the response and never written to a log.

## Two fill modes
A mapping declares `mode:`.

- **fillable** — the PDF has real AcroForm fields. `pdf_fill.py` writes values into
  them by field name. Use `python pdf_fill.py <form>.pdf` to list field names, then
  `scaffold_mapping.py` to generate a starter mapping.
- **overlay** — the PDF is a flat scan with no fields (the common case for FL county
  forms). `pdf_overlay.py` stamps text/marks onto the page. Each entry locates its
  spot one of two ways:
  - `anchor` — finds a label's text via pdfplumber and places the value relative to it
    (resilient to minor form revisions). Only works if the PDF has a text layer.
  - absolute `x`/`y` — an exact PDF coordinate (origin bottom-left). Needed for
    image-only scans with no text layer.

## Why the mapping is the only per-form work
The client model, the context variables, and both engines are generic. Onboarding a
new county or form = writing one `.map.yaml`. No Python changes. That's what keeps the
marginal cost of the next client (and the next county) near zero.

## Context variables
`build_context` exposes these to every mapping (`from: <name>`):

| Variable | Source |
|---|---|
| `first_name` `middle_name` `last_name` `suffix` `full_name` | person |
| `dob` | person (rendered MM/DD/YYYY) |
| `phone` `email` | person's first phone/email |
| `role` | person role (`primary`/`spouse`/`child`/`dependent`) |
| `owner_relationship` | `full_name / <Self|Spouse|Dependent>` |
| `home_line1` `home_line2` `home_city` `home_county` `home_state` `home_zip` | residence address |
| `home_full` | one-line residence address |
| `home_parcel_id` | residence `parcel_id` |
| `case_number` `case_display_name` | case |
| `agent_name` `agent_email` | case (authorized agent) |
| `exemption_code` `exemption_title` `exemption_cite` | case exemption |
| `exemption_category` | `military` \| `law_enforcement` \| `judges` |
| `job_title` `employing_agency` | case exemption (for employment-based redactions) |
| `today` | today's date, MM/DD/YYYY |

To add a variable, extend `build_context` and document it here. `validate_client.py`
picks new variables up automatically: it reads each mapping's `from:` keys and warns
when one resolves empty for a person, so a new mapping needs no validation code.

## The document list

Most of a form is one value per blank, which a mapping can place once and forget. The
Clerk's redaction form also has a **table**, and its length is a property of the client,
not of the form: however many recorded documents name them.

- The rows live on the client as `documents:` — instrument number, book, page, title —
  imported from a county Official Records search by `import_documents.py` or the
  Documents screen. `documents.py` is the only thing that parses or stores them.
- A search matches a name, not a person, so both front ends make you choose which rows
  are the client's: tick boxes and a filter in the app, `--match` on the CLI. The party
  names are parsed (`PARTY_FIELDS`) and shown to make that possible, then dropped —
  `normalize()` keeps `FIELDS`, so no third party's name reaches a client file.
- An overlay mapping declares the geometry in a `tables:` block: which page, the
  baseline of the first ruled line, the pitch to the next, how many lines exist, and a
  column list. `pdf_overlay.table_entries()` turns spec + rows into draw ops.
- Two lists share the store: `redact` (documents to be redacted) and `release` (prior
  redactions being released). A row may carry `persons:` to limit it to some of them.
- **Overflow is not truncation.** When the rows outrun the ruled lines, the last line
  reads "Continued on Attachment A" and `pdf_attachment.py` appends a sheet carrying
  the whole list, with an initial/date line. The form says only the documents it
  identifies get redacted, so a short list would be a defect, not a cosmetic issue.

Nothing in this path fetches anything. The toolkit has no scraper and no Clerk client;
it reads a CSV the worker already has.

## Linked identities
A case holds multiple `persons` (primary, spouse, children). A shared residence flows
to each person's forms, so a spouse's form is `--person spouse` — one command, not a
re-type. The role also drives role-conditional checkboxes (e.g. member/spouse/dependent
on the service-member form; requestor-type on the clerk form).

## No database — on purpose
At the current volume a per-client YAML plus a status spreadsheet is the right tool.
See `docs/decisions.md` (D1) and `docs/future-scale/` for the DB design to graduate to
if/when volume justifies it.
