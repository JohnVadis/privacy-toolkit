# Adding a form (or a county)

Onboarding a form is writing one `forms/mappings/<key>.map.yaml`. No code changes.

## 1. Drop the blank PDF and check its type
```bash
cp <the-form>.pdf forms/_blank_pdfs/
python pdf_fill.py forms/_blank_pdfs/<the-form>.pdf
```
- Lists fields → it's **fillable**. Go to §2.
- Prints nothing / "no fillable fields" → it's a **flat scan**. Go to §3.

## 2. Fillable form
```bash
python scaffold_mapping.py forms/_blank_pdfs/<form>.pdf <form_key>
```
Open the generated `forms/mappings/<form_key>.map.yaml` and set each field to
`{ from: <variable> }`, `{ literal: "..." }`, or for checkboxes
`{ checkbox: true, check: true }` / `{ checkbox: true, when: {field: exemption_category, equals: military} }`.
Variable list: see `docs/architecture.md`.

## 3. Flat scan (overlay mode)
Render it so you can read coordinates, then map text fields by anchor and checkboxes by
coordinate.

```bash
pdftoppm -png -r 100 forms/_blank_pdfs/<form>.pdf /tmp/form
```

### Text fields — prefer anchors
If the scan has a text layer (most do), anchor to the label:
```yaml
- { anchor: "Home Address:", dx: 8, dy: 0, from: home_full }
```
The engine finds that label and stamps just after it. `dx` shifts right, `dy` up.
For a scan with **no** text layer (pure image), use absolute coordinates instead:
```yaml
- { page: 1, x: 172, y: 425, from: home_full }   # PDF points, origin bottom-left
```

### Finding a text field's exact position
```python
import pdfplumber
with pdfplumber.open("forms/_blank_pdfs/<form>.pdf") as pdf:
    pg = pdf.pages[0]; ph = pg.height
    for w in pg.extract_words():
        if "Address" in w["text"]:
            print(w["text"], "x1=", w["x1"], "yb=", ph - w["bottom"])
# place value at x ≈ x1 + a few pts, y ≈ yb
```

### Finding checkbox positions
Checkboxes are drawn either as a font glyph (often Wingdings) or as a vector square.
Locate them and match each to its label (nearest box to the LEFT on the same row):
```python
import pdfplumber
with pdfplumber.open("forms/_blank_pdfs/<form>.pdf") as pdf:
    pg = pdf.pages[0]; ph = pg.height
    # (a) glyph boxes — inspect symbols first:
    #     from collections import Counter
    #     Counter(c["text"] for c in pg.chars if ord(c["text"][0])>0xF000)
    glyph_boxes = [(c["x0"], c["bottom"]) for c in pg.chars if c["text"] == "\uf072"]
    # (b) vector square boxes:
    rect_boxes = [(r["x0"], r["bottom"]) for r in pg.rects
                  if 5 <= r["width"] <= 13 and abs(r["width"] - r["height"]) < 3]
    boxes = glyph_boxes + rect_boxes
    # find your label's (x0, top) via extract_words, then pick the nearest box with
    # box_x0 < label_x0 and |box_top - label_top| < 8. Mark at x ≈ box_x0+1, y ≈ ph-box_bottom+1.
```
Then add:
```yaml
- { page: 1, x: 312, y: 215, mark: "X", when: { field: exemption_category, equals: military } }
```

### A repeating table (the document list)

If the form rules lines for a list of recorded documents, map it as a `tables:` block
instead of one entry per cell. The rules themselves are usually thin rectangles, which
gives you every row's baseline in one go:

```python
import pdfplumber
with pdfplumber.open("forms/_blank_pdfs/<form>.pdf") as pdf:
    pg = pdf.pages[1]; ph = pg.height
    for r in sorted(pg.rects, key=lambda r: r["top"]):
        if r["height"] < 1.5 and r["width"] > 40:          # a ruled line
            print(f'x {r["x0"]:6.1f}-{r["x1"]:6.1f}   baseline y {ph - r["top"] + 2.5:6.1f}')
```
Group them: same `x0`/`x1` repeated down the page is one column; the gap between
consecutive `y` values is `row_pitch`. Then:

```yaml
tables:
  - key: documents_to_redact
    list: redact              # redact | release — which of the client's two lists
    heading: "Documents to be Redacted"
    attachment: "A"           # the letter the overflow sheet is called
    page: 2
    rows: 4                   # how many ruled lines exist
    first_y: 144.9            # baseline of the FIRST one
    row_pitch: 17.4           # how far down to the next
    columns:
      - { from: instrument_number, x: 74,  width: 138 }
      - { from: book,              x: 227, width: 57 }
      - { from: page,              x: 299, width: 57 }
      - { from: title,             x: 371, width: 167 }
```
`width` is what a value gets shrunk (and only then truncated) to fit, so set it to the
ruled line's length. `from:` must be one of `documents.FIELDS`. Overflow is handled for
you: the last ruled line says "Continued on Attachment A" and every row is printed on
an appended sheet — so verify with MORE documents than the form holds, not fewer.

## 4. Adding a statutory category
Each category is one label → one box coordinate → one mapping line. Find the box for
the new category's label (§3), then add a `when: { field: exemption_category, equals: <name> }`
entry to every form that lists it, and document the new category value in
`clients/_TEMPLATE.yaml`.

## 5. Verify
```bash
python fill_forms.py --client clients/example_client.yaml --forms <form_key>
pdftoppm -png -r 100 output/doe_john/<form_key>__primary.pdf /tmp/check
```
Open the PNGs. Every value on its line, every mark in its box. Spot-check each
statutory category the form will be used for (the boxes are close together — a wrong
offset checks the neighbor). Nudge `x`/`y` a few points and re-render until clean.

## Multi-page and image-heavy scans
`pdf_overlay.stamp` clones the source document first and only overlays pages that have
entries — this preserves image-heavy pages (a plain merge can blank them). If you add a
form and a page renders blank, that clone path is why it shouldn't; check the mapping's
`page:` numbers are 1-based and correct.
