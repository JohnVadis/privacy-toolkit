"""The fill engine, end to end on a synthetic form, plus the two drift guards."""
from __future__ import annotations

import pdfplumber
import pytest
import yaml

import fill_forms as FF
from client_context import output_dir


@pytest.fixture
def mapping_file(flat_form, tmp_path):
    """A mapping over the synthetic flat form, with a document table."""
    mapping = {
        "mode": "overlay",
        "form_key": "synthetic",
        "title": "Synthetic test form",
        "pdf": str(flat_form),
        "entries": [
            {"page": 1, "x": 160, "y": 700, "from": "full_name"},
            {"page": 1, "x": 180, "y": 660, "from": "phone"},
            {"page": 1, "x": 72, "y": 580, "mark": "X",
             "when": {"field": "exemption_category", "equals": "military"}},
            {"page": 1, "x": 200, "y": 580, "mark": "X",
             "when": {"field": "exemption_category", "equals": "judges"}},
        ],
        "tables": [{
            "key": "documents", "list": "redact", "heading": "Documents to be Redacted",
            "attachment": "A", "page": 1, "rows": 3,
            "first_y": 602.5, "row_pitch": 18, "size": 9,
            "columns": [
                {"from": "instrument_number", "x": 74, "width": 138},
                {"from": "book", "x": 227, "width": 57},
                {"from": "page", "x": 299, "width": 57},
                {"from": "title", "x": 371, "width": 167},
            ],
        }],
    }
    path = tmp_path / "synthetic.map.yaml"
    path.write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return path


def page_text(pdf_path, index=0):
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[index].extract_text() or ""


def page_count(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


class TestFillOne:
    def test_writes_the_client_values_onto_the_form(self, mapping_file, saved_client):
        out = FF.fill_one(mapping_file, saved_client, None, report=lambda *_: None)
        text = page_text(out)
        assert "Alexandra M Example" in text
        assert "813-555-0175" in text

    def test_names_the_file_by_form_and_person(self, mapping_file, saved_client):
        out = FF.fill_one(mapping_file, saved_client, None, report=lambda *_: None)
        assert out.name == "synthetic__primary.pdf"

    def test_writes_into_this_client_s_own_folder(self, mapping_file, saved_client):
        out = FF.fill_one(mapping_file, saved_client, None, report=lambda *_: None)
        assert out.parent == output_dir(saved_client)

    def test_ticks_only_the_box_matching_the_category(self, mapping_file, saved_client):
        # EXAMPLE_CLIENT is military; the judges box must stay empty.
        out = FF.fill_one(mapping_file, saved_client, None, report=lambda *_: None)
        with pdfplumber.open(out) as pdf:
            marks = [w for w in pdf.pages[0].extract_words() if w["text"] == "X"]
        assert len(marks) == 1
        assert marks[0]["x0"] == pytest.approx(72, abs=2)

    def test_skips_a_mapping_whose_pdf_is_missing(self, tmp_path, saved_client):
        path = tmp_path / "gone.map.yaml"
        path.write_text(yaml.safe_dump(
            {"mode": "overlay", "form_key": "gone", "pdf": "nowhere.pdf",
             "entries": [{"page": 1, "x": 1, "y": 1, "literal": "x"}]}), encoding="utf-8")
        assert FF.fill_one(path, saved_client, None, report=lambda *_: None) is None


class TestDocumentTable:
    def _with_documents(self, client, count):
        import documents as D

        rows = [{"instrument_number": f"20230000{i:02d}", "book": "26841",
                 "page": str(1000 + i), "title": "(D) DEED"} for i in range(count)]
        return D.set_documents(client, rows)

    def test_prints_each_document_on_its_ruled_line(self, mapping_file, saved_client):
        client = self._with_documents(saved_client, 2)
        out = FF.fill_one(mapping_file, client, None, report=lambda *_: None)
        text = page_text(out)
        assert "2023000000" in text and "2023000001" in text
        assert page_count(out) == 1          # no attachment needed

    def test_a_table_with_no_documents_prints_nothing(self, mapping_file, saved_client):
        out = FF.fill_one(mapping_file, saved_client, None, report=lambda *_: None)
        assert page_count(out) == 1

    def test_overflow_says_so_and_attaches_the_whole_list(self, mapping_file,
                                                          saved_client):
        client = self._with_documents(saved_client, 7)
        out = FF.fill_one(mapping_file, client, None, report=lambda *_: None)

        assert "Continued on Attachment A" in page_text(out)
        assert page_count(out) == 2
        attachment = page_text(out, 1)
        assert "ATTACHMENT A" in attachment
        # The form shows a subset; the attachment must carry EVERY document.
        for i in range(7):
            assert f"20230000{i:02d}" in attachment

    def test_the_attachment_is_initialled_by_the_requestor(self, mapping_file,
                                                           saved_client):
        client = self._with_documents(saved_client, 7)
        out = FF.fill_one(mapping_file, client, None, report=lambda *_: None)
        assert "Initials of requestor" in page_text(out, 1)

    def test_release_rows_do_not_appear_in_the_redaction_table(self, mapping_file,
                                                               saved_client):
        import documents as D

        client = D.set_documents(saved_client, [
            {"instrument_number": "REDACT1", "list": "redact"},
            {"instrument_number": "RELEASE1", "list": "release"},
        ])
        out = FF.fill_one(mapping_file, client, None, report=lambda *_: None)
        text = page_text(out)
        assert "REDACT1" in text and "RELEASE1" not in text


class TestOverrideDrift:
    def test_an_override_is_used_when_it_still_points_at_the_same_field(self):
        overrides = {0: {"text": "TYPED BY HAND", "from": "full_name"}}
        entry = {"from": "full_name"}
        assert FF._overridden(overrides, 0, entry, "Generated", print) == "TYPED BY HAND"

    def test_an_override_is_dropped_when_the_mapping_moved_under_it(self):
        overrides = {0: {"text": "TYPED BY HAND", "from": "full_name"}}
        entry = {"from": "phone"}            # entry 0 is a different field now
        notes = []
        value = FF._overridden(overrides, 0, entry, "Generated", notes.append)
        assert value == "Generated" and notes


class TestMarkDrift:
    def test_a_tick_is_honoured_while_it_points_at_the_same_box(self):
        entry = {"page": 2, "x": 73, "y": 601, "mark": "X"}
        marks = {4: {"on": False, "at": FF.mark_signature(entry)}}
        notes = []
        assert FF._mark_state(marks, 4, entry, True, notes.append) is False
        assert not notes

    def test_a_tick_is_dropped_when_the_entry_at_that_index_is_a_different_box(self):
        stored = {"page": 2, "x": 73, "y": 601, "mark": "X"}
        moved = {"page": 2, "x": 289, "y": 471, "mark": "X"}
        marks = {4: {"on": False, "at": FF.mark_signature(stored)}}
        notes = []
        # Falls back to the mapping's own rule rather than un-ticking the wrong box.
        assert FF._mark_state(marks, 4, moved, True, notes.append) is True
        assert notes and "no longer matches" in notes[0]

    def test_a_tick_saved_before_signatures_existed_is_still_honoured(self):
        marks = {4: {"on": False, "at": None}}
        entry = {"page": 2, "x": 73, "y": 601, "mark": "X"}
        assert FF._mark_state(marks, 4, entry, True, print) is False

    def test_no_stored_tick_means_the_mapping_decides(self):
        assert FF._mark_state({}, 4, {"page": 1, "x": 1, "y": 1}, True, print) is True

    def test_the_fill_path_applies_a_stored_tick(self, mapping_file, saved_client):
        mapping = FF.load_mapping(mapping_file)
        judges_entry = mapping["entries"][3]
        saved_client["marks"] = {"synthetic": [
            {"entry": 3, "on": True, "at": FF.mark_signature(judges_entry)}]}

        out = FF.fill_one(mapping_file, saved_client, None, report=lambda *_: None)
        with pdfplumber.open(out) as pdf:
            marks = [w for w in pdf.pages[0].extract_words() if w["text"] == "X"]
        assert len(marks) == 2               # military by rule, judges by hand


class TestHelpers:
    @pytest.mark.parametrize("filename,expected", [
        ("form__primary.pdf", ("form", "primary")),
        ("form__law_enforcement.pdf", ("form", "law_enforcement")),
        ("opt_out_worklist.xlsx", ("opt_out_worklist", "")),
    ])
    def test_split_output_name(self, filename, expected):
        assert FF.split_output_name(filename) == expected

    def test_generated_files_lists_newest_first(self, mapping_file, saved_client):
        FF.fill_one(mapping_file, saved_client, None, report=lambda *_: None)
        rows = FF.generated_files(saved_client)
        assert rows and rows[0]["form_key"] == "synthetic"
        assert rows[0]["is_pdf"]

    def test_generated_files_is_empty_before_anything_is_generated(self, saved_client):
        assert FF.generated_files(saved_client) == []

    def test_list_mappings_reports_a_broken_mapping_instead_of_raising(self, tmp_path,
                                                                      monkeypatch):
        bad = tmp_path / "broken.map.yaml"
        bad.write_text("entries: [unclosed\n", encoding="utf-8")
        monkeypatch.setattr(FF, "mapping_paths", lambda: [bad])
        assert FF.list_mappings()[0]["error"]

    def test_categories_covered_reads_the_when_conditions(self, mapping_file):
        mapping = FF.load_mapping(mapping_file)
        assert set(FF._categories_covered(mapping)) == {"military", "judges"}
