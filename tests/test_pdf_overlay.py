"""Overlay geometry. A wrong number here puts a value in the wrong box, quietly."""
from __future__ import annotations

import pdfplumber
import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth

import pdf_overlay
from pdf_overlay import fit_text, resolve_entries, stamp, table_entries

TABLE = {
    "page": 2,
    "size": 9,
    "first_y": 144.9,
    "row_pitch": 17.4,
    "columns": [
        {"from": "instrument_number", "x": 74, "width": 138},
        {"from": "book", "x": 227, "width": 57},
        {"from": "page", "x": 299, "width": 57},
        {"from": "title", "x": 371, "width": 167},
    ],
}


class TestFitText:
    def test_leaves_a_value_that_fits_alone(self):
        assert fit_text("SHORT", 200, 9) == ("SHORT", 9)

    def test_shrinks_before_it_truncates(self):
        text, size = fit_text("NOTICE OF COMMENCEMENT", 100, 9)
        assert text == "NOTICE OF COMMENCEMENT" and size < 9

    def test_truncates_only_when_shrinking_is_not_enough(self):
        text, size = fit_text("A" * 400, 60, 9)
        assert text.endswith("...") and size >= 6.5

    def test_never_exceeds_the_column(self):
        text, size = fit_text("ASSIGNMENT OF LEASES AND RENTS AND EVERYTHING ELSE", 80, 9)
        assert stringWidth(text, "Helvetica", size) <= 80

    def test_handles_an_empty_or_zero_width_cell(self):
        assert fit_text("", 100, 9) == ("", 9)
        assert fit_text("X", 0, 9) == ("X", 9)


class TestTableEntries:
    def test_converts_the_page_to_a_zero_based_index(self):
        # A mapping's `page:` is 1-based like every other entry; a draw op is an
        # index into the document. Getting this wrong printed the whole document
        # table onto the following page.
        ops = table_entries(TABLE, [{"instrument_number": "2019123456"}])
        assert ops[0]["page"] == 1

    def test_places_each_row_one_pitch_lower(self):
        rows = [{"book": "1"}, {"book": "2"}, {"book": "3"}]
        ys = [op["y"] for op in table_entries(TABLE, rows)]
        assert ys == [144.9, pytest.approx(127.5), pytest.approx(110.1)]

    def test_uses_each_column_x(self):
        ops = table_entries(TABLE, [{"instrument_number": "A", "book": "B",
                                     "page": "C", "title": "D"}])
        assert [op["x"] for op in ops] == [74, 227, 299, 371]

    def test_skips_empty_cells_rather_than_drawing_nothing(self):
        ops = table_entries(TABLE, [{"instrument_number": "A1"}])
        assert len(ops) == 1

    def test_writes_the_continuation_note_on_the_row_after_the_last(self):
        ops = table_entries(TABLE, [{"book": "1"}], note="Continued on Attachment A")
        note = [op for op in ops if "Continued" in str(op["text"])][0]
        assert note["y"] == pytest.approx(144.9 - 17.4)
        assert note["x"] == 74

    def test_the_note_is_allowed_the_full_table_width(self):
        long_note = "Continued on Attachment A — all 237 documents listed there."
        ops = table_entries(TABLE, [], note=long_note)
        assert not ops[0]["text"].endswith("...")      # fits across all four columns

    def test_no_rows_and_no_note_draws_nothing(self):
        assert table_entries(TABLE, []) == []


class TestStamp:
    def test_writes_values_where_the_entries_say(self, flat_form, tmp_path):
        out = tmp_path / "filled.pdf"
        ops = resolve_entries(str(flat_form), [
            {"page": 1, "x": 160, "y": 700, "text": "Alexandra M Example"},
        ])
        stamp(str(flat_form), ops, str(out))

        with pdfplumber.open(out) as pdf:
            words = {w["text"]: w for w in pdf.pages[0].extract_words()}
        assert "Alexandra" in words
        assert words["Alexandra"]["x0"] == pytest.approx(160, abs=1)

    def test_keeps_every_page_of_the_source(self, flat_form, tmp_path):
        out = tmp_path / "filled.pdf"
        stamp(str(flat_form), [], str(out))
        with pdfplumber.open(out) as pdf:
            assert len(pdf.pages) == 1

    def test_appends_attachment_pages_after_the_form(self, flat_form, tmp_path):
        import pdf_attachment

        extra = pdf_attachment.build([{"instrument_number": "A1", "title": "DEED"}],
                                     label="A", heading="Documents to be Redacted")
        out = tmp_path / "filled.pdf"
        stamp(str(flat_form), [], str(out), append=[extra])

        with pdfplumber.open(out) as pdf:
            assert len(pdf.pages) == 2
            assert "ATTACHMENT A" in (pdf.pages[1].extract_text() or "")

    def test_an_anchor_that_is_not_on_the_page_is_skipped_and_reported(self, flat_form):
        notes = []
        ops = resolve_entries(str(flat_form),
                              [{"page": 1, "anchor": "Nowhere:", "text": "x"}],
                              report=notes.append)
        assert ops == [] and notes

    def test_finds_a_value_by_anchor(self, flat_form):
        ops = resolve_entries(str(flat_form),
                              [{"page": 1, "anchor": "Telephone Number:", "dx": 6,
                                "text": "813-555-0175"}])
        assert len(ops) == 1
        assert ops[0]["x"] > 72              # placed after the label, not on it

        # The label was DRAWN at y=660, and the anchor resolves to its bottom —
        # which includes the descender, so it sits ~2pt lower than the baseline.
        # Worth pinning: it is why an anchored entry usually needs a small positive
        # dy to sit on the same line as the printed label.
        assert 656 < ops[0]["y"] < 660

    def test_dy_lifts_an_anchored_value_back_onto_the_label_baseline(self, flat_form):
        plain = resolve_entries(str(flat_form),
                                [{"page": 1, "anchor": "Telephone Number:",
                                  "text": "x"}])
        lifted = resolve_entries(str(flat_form),
                                 [{"page": 1, "anchor": "Telephone Number:", "dy": 2.1,
                                   "text": "x"}])
        assert lifted[0]["y"] == pytest.approx(plain[0]["y"] + 2.1)


class TestMarkSignature:
    def test_distinguishes_boxes_on_the_same_page(self):
        from fill_forms import mark_signature

        a = {"page": 1, "x": 55, "y": 564, "mark": "X"}
        b = {"page": 1, "x": 55, "y": 349, "mark": "X"}
        assert mark_signature(a) != mark_signature(b)

    def test_is_stable_for_the_same_box(self):
        from fill_forms import mark_signature

        entry = {"page": 2, "x": 73, "y": 601, "mark": "X"}
        assert mark_signature(entry) == mark_signature(dict(entry))

    def test_covers_anchored_marks_too(self):
        from fill_forms import mark_signature

        a = {"page": 1, "anchor": "COUNTY OF", "dx": 6, "mark": "X"}
        b = {"page": 1, "anchor": "COUNTY OF", "dx": 40, "mark": "X"}
        assert mark_signature(a) != mark_signature(b)
