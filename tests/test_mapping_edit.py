"""The mapping checker — the guard in front of the highest-leverage files here.

A mapping is written once and then used for every client and every filing of that
form. A wrong coordinate does not fail loudly; it produces a government form with a
mark in the wrong box, for everyone, until a human notices.
"""
from __future__ import annotations

import pytest
import yaml

import paths
from fill_forms import form_key_for, mapping_paths
from mapping_edit import check_mapping

BASE = {
    "form_key": "synthetic",
    "mode": "overlay",
    "entries": [{"page": 1, "x": 72, "y": 700, "from": "full_name"}],
}


def mapping(**overrides):
    m = dict(BASE)
    m.update(overrides)
    return yaml.safe_dump(m)


def problems(text, expect_key=None):
    return check_mapping(text, expect_key)[1]


class TestShippedMappings:
    """The regression that matters: every mapping in the repo still checks clean."""

    @pytest.mark.parametrize("path", mapping_paths(), ids=form_key_for)
    def test_checks_clean(self, path):
        text = path.read_text(encoding="utf-8")
        found = problems(text, form_key_for(path))

        # "No blank PDF at ..." is about the machine, not the mapping: some blank
        # forms are gitignored, so a fresh clone (and CI) legitimately lacks them.
        # Whether the file is THERE is the next test's job; this one is structure.
        found = [p for p in found if not p.startswith("No blank PDF at ")]
        assert found == []

    @pytest.mark.parametrize("path", mapping_paths(), ids=form_key_for)
    def test_declares_a_blank_pdf_that_exists(self, path):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        pdf = paths.resolve(data["pdf"])
        if not pdf.is_file():
            pytest.skip(f"{pdf.name} is gitignored and not present")
        assert pdf.stat().st_size > 0


class TestStructure:
    def test_accepts_a_sound_mapping(self, flat_form):
        assert problems(mapping(pdf=str(flat_form))) == []

    def test_requires_a_form_key(self, flat_form):
        text = yaml.safe_dump({"mode": "overlay", "pdf": str(flat_form),
                               "entries": BASE["entries"]})
        assert any("form_key is required" in p for p in problems(text))

    def test_form_key_must_match_the_filename(self, flat_form):
        found = problems(mapping(pdf=str(flat_form)), "a_different_key")
        assert any("must match" in p for p in found)

    def test_rejects_an_unknown_top_level_key(self, flat_form):
        found = problems(mapping(pdf=str(flat_form), colour="blue"))
        assert any("Unknown setting 'colour'" in p for p in found)

    def test_rejects_an_unknown_mode(self, flat_form):
        assert any("mode must be" in p for p in problems(mapping(pdf=str(flat_form),
                                                                 mode="magic")))

    def test_reports_a_missing_pdf(self):
        assert any("No blank PDF" in p for p in problems(mapping(pdf="nowhere.pdf")))

    def test_rejects_invalid_yaml(self):
        data, found = check_mapping("entries: [unclosed\n")
        assert data is None and found

    def test_an_overlay_mapping_needs_entries(self, flat_form):
        text = yaml.safe_dump({"form_key": "x", "mode": "overlay",
                               "pdf": str(flat_form), "entries": []})
        assert any("needs a list of entries" in p for p in problems(text))


class TestEntries:
    def test_an_entry_needs_somewhere_to_go(self, flat_form):
        found = problems(mapping(pdf=str(flat_form),
                                 entries=[{"from": "full_name"}]))
        assert any("needs either an anchor" in p for p in found)

    def test_an_entry_needs_something_to_write(self, flat_form):
        found = problems(mapping(pdf=str(flat_form), entries=[{"page": 1, "x": 1, "y": 1}]))
        assert any("needs from:, literal: or mark:" in p for p in found)

    def test_rejects_an_unknown_entry_setting(self, flat_form):
        found = problems(mapping(pdf=str(flat_form),
                                 entries=[{"page": 1, "x": 1, "y": 1, "from": "a",
                                           "colour": "red"}]))
        assert any("unknown setting 'colour'" in p for p in found)

    def test_rejects_a_coordinate_off_the_page(self, flat_form):
        found = problems(mapping(pdf=str(flat_form),
                                 entries=[{"page": 1, "x": 5000, "y": 5000,
                                           "from": "a"}]))
        assert any("off the page" in p for p in found)

    def test_rejects_a_page_the_form_does_not_have(self, flat_form):
        found = problems(mapping(pdf=str(flat_form),
                                 entries=[{"page": 9, "x": 1, "y": 1, "from": "a"}]))
        assert any("but the form has" in p for p in found)


class TestTables:
    def table(self, **overrides):
        t = {"key": "documents", "list": "redact", "page": 1, "rows": 3,
             "first_y": 600, "row_pitch": 18,
             "columns": [{"from": "instrument_number", "x": 74, "width": 138}]}
        t.update(overrides)
        return t

    def test_accepts_a_sound_table(self, flat_form):
        assert problems(mapping(pdf=str(flat_form), tables=[self.table()])) == []

    def test_rejects_an_unknown_table_setting(self, flat_form):
        found = problems(mapping(pdf=str(flat_form),
                                 tables=[self.table(colour="red")]))
        assert any("unknown setting 'colour'" in p for p in found)

    def test_rejects_an_unknown_list_name(self, flat_form):
        found = problems(mapping(pdf=str(flat_form), tables=[self.table(list="other")]))
        assert any("list: must be one of" in p for p in found)

    def test_requires_at_least_one_row(self, flat_form):
        found = problems(mapping(pdf=str(flat_form), tables=[self.table(rows=0)]))
        assert any("at least 1" in p for p in found)

    def test_rejects_rows_that_walk_off_the_page(self, flat_form):
        # first_y 100 with pitch 60 over 3 rows ends below the page edge.
        found = problems(mapping(pdf=str(flat_form),
                                 tables=[self.table(first_y=100, row_pitch=60)]))
        assert any("off a" in p for p in found)

    def test_rejects_a_column_bound_to_an_unknown_field(self, flat_form):
        found = problems(mapping(pdf=str(flat_form), tables=[
            self.table(columns=[{"from": "nonsense", "x": 74}])]))
        assert any("from: must be one of" in p for p in found)

    def test_requires_columns(self, flat_form):
        found = problems(mapping(pdf=str(flat_form), tables=[self.table(columns=[])]))
        assert any("needs a columns: list" in p for p in found)

    def test_tables_only_work_in_overlay_mode(self, flat_form):
        text = yaml.safe_dump({"form_key": "x", "mode": "fillable",
                               "pdf": str(flat_form), "fields": {},
                               "tables": [self.table()]})
        assert any("only works in overlay mode" in p for p in problems(text))
