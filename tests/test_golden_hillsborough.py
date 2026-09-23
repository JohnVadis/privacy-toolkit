"""Golden test: the real Clerk redaction form, filled, checked value by value.

This is the regression that protects an actual filing. Everything else here tests a
function; this asserts that a named value lands at a known spot on the county's own
PDF, so an accidental nudge to a coordinate fails a test instead of producing a form
with the wrong box ticked.

The blank PDFs are gitignored, so this skips on a fresh clone. Run it on a machine
that has them — which is any machine that can actually file.
"""
from __future__ import annotations

import pdfplumber
import pytest

import documents as D
import fill_forms as FF
from conftest import blank_pdf

FORM = "hillsborough_clerk_redaction"


@pytest.fixture
def filled(saved_client):
    blank_pdf("Hillsborough_Redaction.pdf")          # skips if it isn't there
    path = FF.mapping_paths()
    mapping = next(p for p in path if FF.form_key_for(p) == FORM)
    return FF.fill_one(mapping, saved_client, None, report=lambda *_: None)


@pytest.fixture
def filled_with_documents(saved_client):
    blank_pdf("Hillsborough_Redaction.pdf")
    client = D.set_documents(saved_client, [
        {"instrument_number": "2023111222", "title": "(D) DEED",
         "recorded": "2023-08-14"},
    ])
    mapping = next(p for p in FF.mapping_paths() if FF.form_key_for(p) == FORM)
    return FF.fill_one(mapping, client, None, report=lambda *_: None)


# A stamped value is extracted at its glyph BOTTOM, a couple of points below the
# baseline the mapping drew it on, so every coordinate here is checked to ~4pt.
# That is still far tighter than the gap between adjacent boxes on this form.
PLACEMENT = 4


def words_on(pdf_path, page_index):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        height = page.height
        return [{"text": w["text"], "x": w["x0"], "y": height - w["bottom"]}
                for w in page.extract_words()]


def find(words, text):
    """The first word whose text matches, or None."""
    return next((w for w in words if w["text"] == text), None)


def find_like(words, text):
    """Same, ignoring the form's own printed rule.

    A value written onto a ruled blank is extracted merged with the underscores
    beneath it — "813-555-0175" comes back as "_8_1_3_-5__5_5_-0_1_7_5_____".
    """
    wanted = text.replace("_", "")
    return next((w for w in words if w["text"].replace("_", "") == wanted), None)


def marks_on(pdf_path, page_index):
    """The X marks the engine stamped.

    Each one lands on top of the form's own checkbox glyph and pdfplumber merges
    the two into a single token ("X"), so matching on == "X" finds nothing.
    """
    return [w for w in words_on(pdf_path, page_index)
            if w["text"].strip().endswith("X") and len(w["text"].strip()) <= 2]


class TestStructure:
    def test_keeps_all_four_pages(self, filled):
        with pdfplumber.open(filled) as pdf:
            assert len(pdf.pages) == 4

    def test_adds_no_attachment_when_the_list_fits(self, filled_with_documents):
        with pdfplumber.open(filled_with_documents) as pdf:
            assert len(pdf.pages) == 4


class TestPageTwoContactBlock:
    def test_printed_name_sits_on_its_line(self, filled):
        word = find(words_on(filled, 1), "Alexandra")
        assert word and word["x"] == pytest.approx(142, abs=PLACEMENT)
        assert word["y"] == pytest.approx(685, abs=PLACEMENT)

    def test_telephone_sits_on_its_line(self, filled):
        word = find_like(words_on(filled, 1), "813-555-0175")
        assert word and word["x"] == pytest.approx(166, abs=PLACEMENT)
        assert word["y"] == pytest.approx(652, abs=PLACEMENT)

    def test_email_sits_on_its_line(self, filled):
        word = find(words_on(filled, 1), "alex.example@example.com")
        assert word and word["x"] == pytest.approx(321, abs=PLACEMENT)

    def test_date_of_birth_is_rendered_mm_dd_yyyy_in_its_box(self, filled):
        word = find(words_on(filled, 1), "04/17/1986")
        assert word and word["x"] == pytest.approx(365, abs=PLACEMENT)
        assert word["y"] == pytest.approx(469, abs=PLACEMENT)


# Page 1 carries two independent groups of boxes.
REQUESTOR_TYPE = {"primary": (73, 683), "spouse": (73, 670), "child": (73, 656)}
CATEGORY_BOX = {"law_enforcement": (55, 564), "judges": (55, 349),
                "military": (312, 215)}


def ticked_at(marks, x, y):
    return any(abs(m["x"] - x) < PLACEMENT and abs(m["y"] - y) < PLACEMENT
               for m in marks)


class TestPageOneBoxes:
    """The boxes sit close together — a wrong offset ticks the neighbour."""

    def test_ticks_the_requestor_type_and_the_category_and_nothing_else(self, filled):
        marks = marks_on(filled, 0)
        assert len(marks) == 2
        assert ticked_at(marks, *REQUESTOR_TYPE["primary"])
        assert ticked_at(marks, *CATEGORY_BOX["military"])

    def test_the_requestor_type_follows_the_person_s_role(self, saved_client):
        blank_pdf("Hillsborough_Redaction.pdf")
        saved_client["persons"].append({
            "role": "spouse", "first_name": "Sam", "last_name": "Example",
            "dob": "1985-01-02", "phones": ["813-555-0100"], "emails": ["s@x.invalid"],
            "addresses": saved_client["persons"][0]["addresses"]})
        mapping = next(p for p in FF.mapping_paths() if FF.form_key_for(p) == FORM)
        out = FF.fill_one(mapping, saved_client, "spouse", report=lambda *_: None)

        marks = marks_on(out, 0)
        assert ticked_at(marks, *REQUESTOR_TYPE["spouse"])
        assert not ticked_at(marks, *REQUESTOR_TYPE["primary"])

    @pytest.mark.parametrize("category", list(CATEGORY_BOX))
    def test_each_category_ticks_its_own_box_and_not_the_others(self, saved_client,
                                                                category):
        blank_pdf("Hillsborough_Redaction.pdf")
        saved_client["case"]["exemption"]["category"] = category
        mapping = next(p for p in FF.mapping_paths() if FF.form_key_for(p) == FORM)
        out = FF.fill_one(mapping, saved_client, None, report=lambda *_: None)

        marks = marks_on(out, 0)
        assert ticked_at(marks, *CATEGORY_BOX[category])
        for other, spot in CATEGORY_BOX.items():
            if other != category:
                assert not ticked_at(marks, *spot), f"{other} box was ticked too"


class TestInformationToBeRedacted:
    def test_address_phone_and_dob_boxes_are_ticked(self, filled):
        marks = marks_on(filled, 1)
        assert ticked_at(marks, 73, 601)     # address where I reside
        assert ticked_at(marks, 73, 487)     # telephone number(s)
        assert ticked_at(marks, 289, 471)    # date of birth

    def test_the_ssn_box_is_left_alone_by_design(self, filled):
        # The form says do not list an SSN; the toolkit never stores one either.
        assert not ticked_at(marks_on(filled, 1), 73, 471)


class TestDocumentTable:
    def test_a_document_lands_on_the_first_ruled_line(self, filled_with_documents):
        word = find(words_on(filled_with_documents, 1), "2023111222")
        assert word and word["x"] == pytest.approx(74, abs=PLACEMENT)
        assert word["y"] == pytest.approx(144.9, abs=PLACEMENT)

    def test_the_title_lands_in_the_title_column(self, filled_with_documents):
        word = find(words_on(filled_with_documents, 1), "DEED")
        assert word and word["x"] > 371

    def test_book_and_page_print_blank_for_an_instrument_only_record(
            self, filled_with_documents):
        # Modern Hillsborough records have no book/page; those columns stay empty.
        row = [w for w in words_on(filled_with_documents, 1)
               if abs(w["y"] - 144.9) < 2]
        assert not any(220 < w["x"] < 360 for w in row)


class TestOverflow:
    def test_more_documents_than_lines_attaches_the_whole_list(self, saved_client):
        blank_pdf("Hillsborough_Redaction.pdf")
        rows = [{"instrument_number": f"202300{i:04d}", "title": "(D) DEED"}
                for i in range(7)]
        client = D.set_documents(saved_client, rows)
        mapping = next(p for p in FF.mapping_paths() if FF.form_key_for(p) == FORM)
        out = FF.fill_one(mapping, client, None, report=lambda *_: None)

        with pdfplumber.open(out) as pdf:
            assert len(pdf.pages) == 5                      # 4 + one attachment
            page_two = pdf.pages[1].extract_text() or ""
            attachment = pdf.pages[4].extract_text() or ""

        assert "Continued on Attachment A" in page_two
        for i in range(7):
            assert f"202300{i:04d}" in attachment
