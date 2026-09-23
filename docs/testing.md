# Testing

```bash
pip install -r requirements-dev.txt
pytest                                      # the whole suite, ~11s
pytest tests/test_golden_hillsborough.py    # just the real-form coordinate checks
pytest -k worklist                          # one area
```

## The suite never touches real client data

`tests/conftest.py` points `paths.ROOT` at a temporary directory — via the
`PRIVACY_TOOLKIT_HOME` environment variable — **before any toolkit module is
imported**, and asserts the redirect took effect. `forms/`, `sites.yaml` and the
web app's `templates/` and `static/` are copied in, so mappings resolve and pages
render normally. (The Python package itself is still imported from the repo.)

That means the tests exercise the real save, delete and fill paths rather than
mocks, while a bug in one of those paths destroys a temp folder instead of
someone's case files. Every test also gets `clients/`, `output/` and `trash/`
emptied around it, so test order never matters.

Nothing in the suite reads a real client file. Fixtures build on
`client_context.EXAMPLE_CLIENT`, the fictional client the toolkit already ships
for previews.

## What each file covers

| File | What it protects |
|---|---|
| `test_paths.py` | That a folder name can never be empty, reserved, or resolve to `output/` itself. These are the guards behind the delete-everything bug. |
| `test_client_context.py` | Context building (what actually reaches a form), date rendering, atomic save + backups, and that two clients sharing a display name get different folders. |
| `test_documents.py` | Every header shape a county export throws at the parser, including the real Hillsborough camelCase one, and that party names are shown but never stored. |
| `test_pdf_overlay.py` | Geometry: text fitting, table row pitch, 1-based page → 0-based index, anchors. |
| `test_fill_forms.py` | The engine end to end on a synthetic form, table overflow and attachments, and both drift guards. |
| `test_build_worklist.py` | That rebuilding never loses tracked work — the spreadsheet IS the tracker. |
| `test_validate_client.py` | That validation blocks what it should, warns about the rest, and never decides eligibility. |
| `test_mapping_edit.py` | That **every shipped mapping still checks clean**, plus the structural rules. |
| `test_golden_hillsborough.py` | The one that protects a filing: named values at known coordinates on the county's own PDF. |
| `test_webapp_security.py` | The Host / cross-site / token gate, check by check. Golden rule #7 says don't weaken it; this is what makes weakening it fail. |
| `test_webapp_model.py` | Posted form data → client file: the bracket convention, sparse rows, the residence radio, and that unmanaged keys survive a save. |
| `test_webapp_routes.py` | Create / edit / delete / generate / worklist through the real app, plus the slug and filename guards. |
| `test_webapp_documents.py` | The import and the row picker: preview saves nothing, none-ticked is refused, party names never reach the file. |
| `test_webapp_corrections.py` | Hand edits stored against the client, and both drift guards end to end through the route. |
| `test_webapp_forms.py` | The mapping editor and form importer: that a bad save writes nothing, that an edit can't destroy the rest of the file, and that an upload is judged on contents. |
| `test_feedback.py` | The Comment feature, and above all that it never sends. |
| `test_generation_log.py` | That every filing is journalled, that a tampered file is detectable, and that a log failure never costs you the form. |
| `test_housekeeping.py` | Retention: what gets pruned, what doesn't, and that nothing is ever removed without being asked. |
| `test_case_export.py` | Export, verify and restore — including that a zip naming a member `../../anything` is refused rather than unpacked. |

## The golden test

`test_golden_hillsborough.py` fills the real Clerk redaction form and asserts where
each value landed — the printed name at x≈142, the military box at (312, 215), the
first document on the ruled line at y≈144.9 — and, just as importantly, that the
boxes which must stay empty are empty (the SSN box, the two statutory categories the
client doesn't claim).

It skips when `forms/_blank_pdfs/` is empty, because those PDFs are gitignored. So it
does not run in CI and **does** run on any machine that can actually file. Run it
before a filing after touching a mapping.

Two things about the output are worth knowing, because they trip up anyone writing a
new assertion:

- A stamped `X` lands on top of the form's own checkbox glyph, and pdfplumber merges
  the two into one token (`"X"`), so matching on `== "X"` finds nothing.
- A value written onto a ruled blank merges with the underscores beneath it —
  `813-555-0175` extracts as `_8_1_3_-5__5_5_-0_1_7_5_____`. `find_like()` handles it.
- Extracted `y` is the glyph *bottom*, two or three points below the baseline the
  mapping drew on, which is why coordinates are checked to ~4pt rather than exactly.

## The web app

`app_client` is a `TestClient` holding this session's token, with a loopback
`base_url` — TestClient's default `testserver` is exactly the foreign Host the gate
exists to refuse. `raw_client` has no token, for testing the gate itself.

The route tests drive the real app: they write client files, generate PDFs and build
spreadsheets, all inside the sandbox. Nothing is mocked, so a broken route fails here
rather than in front of a worker.

## The Comment feature

`test_feedback.py` replaces the screenshot grab, the mail-client launch and the
file-manager reveal, because a suite that shells out to the desktop is a suite nobody
runs. What it pins is the property the feature rests on: **it never sends.** One test
reads the source of both modules and fails if either grows a `requests`, `httpx`,
`smtplib` or raw-socket call. The rest check that sending only builds a compose URL
and opens it, that the comment travels in that URL rather than over the wire, and that
the folder path — which carries the sender's user name on Windows — stays out of the
body.

## Adding to it

Prefer a test that would have caught a real defect. Every case in
`test_build_worklist.py::TestRebuildPreservesWork` exists because rebuilding used to
wipe the sheet; the drift tests exist because a reordered mapping used to move a
hand-set tick. Two of the tests here found live bugs the day they were written — an
unreachable error branch in `documents.decode`, and a tracking check that counted
every worklist row as worked-on.

The second round found two more. The mapping editor returned a **500 on every
refused save**, because the error path forgot a template variable that gets fed to
`|tojson` — so the one moment a worker needs their work handed back with the reason
on it was the one moment the page broke. And `_entry_spans` treated the entries block
as running to the end of the file, so editing or deleting the **last** entry of a
mapping silently deleted everything after it — which, on the real Clerk mapping, is
the entire `tables:` block that prints the document list. `check_mapping` accepted the
result, because a mapping without a table is still valid. Saving a comment twice in
the same second also collided, pairing one screenshot with another comment's words.

Writing them also turned up a gap worth knowing about: if a mapping edit leaves a
stored tick's index pointing at something that is no longer a tick box at all, the
tick is quietly ignored rather than reported. It cannot mark the wrong box, so it is
safe — but it is silent, and
`test_webapp_corrections.py::test_a_tick_is_ignored_when_its_index_is_no_longer_a_box_at_all`
pins that behaviour so a future change is a deliberate one.
