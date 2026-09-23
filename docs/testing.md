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
imported**, and asserts the redirect took effect. `forms/` and `sites.yaml` are
copied in so mappings resolve normally.

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

## Adding to it

Prefer a test that would have caught a real defect. Every case in
`test_build_worklist.py::TestRebuildPreservesWork` exists because rebuilding used to
wipe the sheet; the drift tests exist because a reordered mapping used to move a
hand-set tick. Two of the tests here found live bugs the day they were written — an
unreachable error branch in `documents.decode`, and a tracking check that counted
every worklist row as worked-on.
