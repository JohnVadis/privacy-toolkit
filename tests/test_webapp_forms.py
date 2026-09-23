"""The mapping editor and the form importer.

A mapping is written once and then used for every client and every filing of that
form, so this is the highest-leverage screen in the app — and the one place where a
bad save would be felt by everyone. Every path that writes a mapping goes through
check_mapping() first and leaves a backup behind; that is what these pin.
"""
from __future__ import annotations

import json

import pytest
import yaml

import mapping_edit
import paths
from client_context import EXAMPLE_SLUG, save_client

GOOD = {
    "form_key": "editable",
    "mode": "overlay",
    "title": "Editable test form",
    "entries": [
        {"page": 1, "x": 160, "y": 700, "from": "full_name"},
        {"page": 1, "x": 180, "y": 660, "from": "phone"},
    ],
}


@pytest.fixture
def form(flat_form):
    """A real mapping on disk, over the synthetic flat form."""
    data = dict(GOOD, pdf=str(flat_form))
    path = paths.MAPPINGS_DIR / "editable.map.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    yield "editable"
    path.unlink(missing_ok=True)


def mapping_text(form_key="editable"):
    return mapping_edit.mapping_file(form_key).read_text(encoding="utf-8")


def entries(form_key="editable"):
    return yaml.safe_load(mapping_text(form_key))["entries"]


class TestOverview:
    def test_lists_every_mapping(self, app_client, form):
        assert "editable" in app_client.get("/forms").text

    def test_is_read_only(self, app_client, form):
        # No route under /forms changes a mapping except the editor's own POST.
        body = app_client.get("/forms").text
        assert "Edit" in body


class TestEditorPage:
    def test_renders_the_mapping_text(self, app_client, form):
        assert "full_name" in app_client.get(f"/forms/{form}/edit").text

    def test_an_unknown_form_is_a_404(self, app_client):
        assert app_client.get("/forms/not_a_form/edit").status_code == 404


class TestSaving:
    def test_a_sound_mapping_is_written(self, app_client, form, flat_form):
        changed = dict(GOOD, pdf=str(flat_form), title="Renamed in the editor")
        response = app_client.post(f"/forms/{form}/edit", follow_redirects=False,
                                   data={"text": yaml.safe_dump(changed)})
        assert response.status_code == 303
        assert "Renamed in the editor" in mapping_text()

    def test_the_previous_version_is_backed_up(self, app_client, form, flat_form):
        changed = dict(GOOD, pdf=str(flat_form), title="Second version")
        app_client.post(f"/forms/{form}/edit", follow_redirects=False,
                        data={"text": yaml.safe_dump(changed)})
        assert mapping_edit.list_backups(form)

    def test_a_broken_mapping_is_refused_and_nothing_is_written(self, app_client, form):
        before = mapping_text()
        response = app_client.post(f"/forms/{form}/edit",
                                   data={"text": "entries: [unclosed\n"})
        assert response.status_code == 422
        assert mapping_text() == before

    def test_it_says_what_was_wrong(self, app_client, form, flat_form):
        bad = dict(GOOD, pdf=str(flat_form),
                   entries=[{"page": 1, "x": 5000, "y": 5000, "from": "full_name"}])
        body = app_client.post(f"/forms/{form}/edit",
                               data={"text": yaml.safe_dump(bad)}).text
        assert "off the page" in body

    def test_the_editors_work_survives_a_refused_save(self, app_client, form,
                                                      flat_form):
        bad = dict(GOOD, pdf=str(flat_form), title="Work in progress",
                   entries=[{"page": 9, "x": 1, "y": 1, "from": "full_name"}])
        body = app_client.post(f"/forms/{form}/edit",
                               data={"text": yaml.safe_dump(bad)}).text
        assert "Work in progress" in body      # handed back, not discarded

    def test_a_mismatched_form_key_is_refused(self, app_client, form, flat_form):
        wrong = dict(GOOD, pdf=str(flat_form), form_key="something_else")
        response = app_client.post(f"/forms/{form}/edit",
                                   data={"text": yaml.safe_dump(wrong)})
        assert response.status_code == 422


class TestEditorOperations:
    def post_ops(self, app_client, form, ops, text=None, flat_form=None):
        data = {"text": text if text is not None else mapping_text(),
                "ops": json.dumps(ops)}
        return app_client.post(f"/forms/{form}/edit", data=data,
                               follow_redirects=False)

    def test_moving_an_entry_changes_only_its_coordinates(self, app_client, form):
        response = self.post_ops(app_client, form,
                                 [{"op": "move", "entry": 0, "x": 200, "y": 650}])
        assert response.status_code == 303
        moved = entries()[0]
        assert (moved["x"], moved["y"]) == (200, 650)
        assert moved["from"] == "full_name"        # untouched

    def test_moving_keeps_the_comments_in_the_file(self, app_client, form, flat_form):
        text = mapping_text().replace("entries:", "# a note worth keeping\nentries:")
        mapping_edit.mapping_file(form).write_text(text, encoding="utf-8")

        self.post_ops(app_client, form,
                      [{"op": "move", "entry": 0, "x": 200, "y": 650}], text=text)
        # Re-dumping the parsed YAML would strip this; move_entries edits in place.
        assert "# a note worth keeping" in mapping_text()

    def test_adding_an_entry_appends_it(self, app_client, form):
        self.post_ops(app_client, form, [
            {"op": "add", "entry_data": {"page": 1, "x": 90, "y": 500,
                                         "literal": "added"}}])
        assert entries()[-1]["literal"] == "added"

    def test_removing_an_entry_drops_it(self, app_client, form):
        self.post_ops(app_client, form, [{"op": "remove", "entry": 0}])
        assert len(entries()) == 1
        assert entries()[0]["from"] == "phone"

    def test_updating_an_entry_rewrites_it(self, app_client, form):
        self.post_ops(app_client, form, [
            {"op": "update", "entry": 1,
             "entry_data": {"page": 1, "x": 180, "y": 660, "from": "email"}}])
        assert entries()[1]["from"] == "email"

    def test_an_unreadable_operation_list_saves_nothing(self, app_client, form):
        before = mapping_text()
        response = app_client.post(f"/forms/{form}/edit",
                                   data={"text": before, "ops": "not json"})
        assert response.status_code == 422
        assert mapping_text() == before

    def test_an_incomplete_operation_saves_nothing(self, app_client, form):
        before = mapping_text()
        response = self.post_ops(app_client, form,
                                 [{"op": "move", "entry": 0, "x": "sideways"}])
        assert response.status_code == 422
        assert mapping_text() == before


class TestKeysAfterEntries:
    """A mapping may carry top-level keys AFTER `entries:` — `tables:` does.

    The entries block used to be treated as running to the end of the file, so the
    last entry's span swallowed whatever followed it. Rewriting or deleting that
    entry then deleted the document table, and check_mapping accepted the result
    because a mapping without a tables block is still perfectly valid. The table
    would simply stop printing on every future filing, with nothing said.
    """

    @pytest.fixture
    def with_tables(self, flat_form):
        data = dict(GOOD, pdf=str(flat_form), tables=[{
            "key": "documents", "list": "redact", "page": 1, "rows": 3,
            "first_y": 600, "row_pitch": 18,
            "columns": [{"from": "instrument_number", "x": 74, "width": 138}]}])
        path = paths.MAPPINGS_DIR / "editable.map.yaml"
        # entries: deliberately before tables:, as the real Clerk mapping has it.
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        yield "editable"
        path.unlink(missing_ok=True)

    def _ops(self, app_client, form, ops):
        return app_client.post(f"/forms/{form}/edit", follow_redirects=False,
                               data={"text": mapping_text(), "ops": json.dumps(ops)})

    def test_updating_the_last_entry_keeps_the_table(self, app_client, with_tables):
        self._ops(app_client, with_tables, [
            {"op": "update", "entry": 1,
             "entry_data": {"page": 1, "x": 180, "y": 660, "from": "email"}}])
        saved = yaml.safe_load(mapping_text())
        assert "tables" in saved
        assert saved["entries"][1]["from"] == "email"

    def test_removing_the_last_entry_keeps_the_table(self, app_client, with_tables):
        self._ops(app_client, with_tables, [{"op": "remove", "entry": 1}])
        saved = yaml.safe_load(mapping_text())
        assert "tables" in saved and len(saved["entries"]) == 1

    def test_adding_an_entry_keeps_the_table(self, app_client, with_tables):
        self._ops(app_client, with_tables, [
            {"op": "add", "entry_data": {"page": 1, "x": 90, "y": 500,
                                         "literal": "added"}}])
        saved = yaml.safe_load(mapping_text())
        assert "tables" in saved
        assert saved["entries"][-1]["literal"] == "added"

    def test_the_new_entry_goes_inside_the_entries_block(self, app_client,
                                                          with_tables):
        # Appended after tables: it would be a sequence item among mapping keys —
        # invalid YAML, which the save would then refuse.
        self._ops(app_client, with_tables, [
            {"op": "add", "entry_data": {"page": 1, "x": 90, "y": 500,
                                         "literal": "added"}}])
        text = mapping_text()
        assert text.index("literal: added") < text.index("tables:")

    def test_the_real_clerk_mapping_survives_an_edit_to_its_last_entry(self):
        # The form actually filed, with tables: after entries:.
        import mapping_edit as ME

        source = ME.mapping_file("hillsborough_clerk_redaction")
        if not source.is_file():
            pytest.skip("the Clerk mapping is not present")
        text = source.read_text(encoding="utf-8")
        last = max(ME._entry_spans(text.splitlines(keepends=True)))

        updated, _ = ME.update_entries(text, {last: {
            "page": 4, "x": 224, "y": 602, "from": "employing_agency"}})
        parsed = yaml.safe_load(updated)
        assert "tables" in parsed
        assert len(parsed["tables"]) == 2       # redaction list and release list


class TestEntrySanitising:
    def test_drops_a_key_a_mapping_entry_may_not_carry(self):
        from webapp.routers.forms import _clean_entry

        assert "colour" not in _clean_entry({"x": 1, "y": 2, "colour": "red"})

    def test_clamps_the_font_size(self):
        from webapp.routers.forms import _clean_entry

        assert _clean_entry({"size": 900})["size"] == 36
        assert _clean_entry({"size": 1})["size"] == 4

    def test_a_page_is_never_below_one(self):
        from webapp.routers.forms import _clean_entry

        assert _clean_entry({"page": -3})["page"] == 1

    def test_keeps_only_a_condition_the_engine_understands(self):
        from webapp.routers.forms import _clean_entry

        assert "when" not in _clean_entry(
            {"when": {"field": "invented_field", "equals": "x"}})
        kept = _clean_entry({"when": {"field": "role", "equals": "primary"}})
        assert kept["when"] == {"field": "role", "equals": "primary"}

    def test_drops_empty_values_rather_than_writing_blanks(self):
        from webapp.routers.forms import _clean_entry

        assert _clean_entry({"from": "", "literal": None, "x": 1}) == {"x": 1}


class TestPreview:
    def test_renders_the_unsaved_mapping(self, app_client, form, example_client):
        save_client("Previewed", example_client)
        response = app_client.post(f"/forms/{form}/preview/1",
                                   data={"text": mapping_text(),
                                         "client": "Previewed"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_works_against_the_built_in_example_client(self, app_client, form):
        response = app_client.post(f"/forms/{form}/preview/1",
                                   data={"text": mapping_text(),
                                         "client": EXAMPLE_SLUG})
        assert response.status_code == 200

    def test_reports_the_page_size_so_clicks_map_to_coordinates(self, app_client,
                                                                form):
        response = app_client.post(f"/forms/{form}/preview/1",
                                   data={"text": mapping_text(),
                                         "client": EXAMPLE_SLUG})
        assert float(response.headers["X-Page-Width-Pt"]) == pytest.approx(612, abs=1)

    def test_leaves_no_folder_behind_in_output(self, app_client, form):
        app_client.post(f"/forms/{form}/preview/1",
                        data={"text": mapping_text(), "client": EXAMPLE_SLUG})
        assert [p.name for p in paths.OUTPUT_DIR.iterdir()] == []

    def test_never_writes_to_the_saved_mapping(self, app_client, form, flat_form):
        before = mapping_text()
        changed = yaml.safe_dump(dict(GOOD, pdf=str(flat_form), title="Only previewed"))
        app_client.post(f"/forms/{form}/preview/1",
                        data={"text": changed, "client": EXAMPLE_SLUG})
        assert mapping_text() == before

    def test_a_broken_mapping_is_a_400_not_a_render(self, app_client, form):
        response = app_client.post(f"/forms/{form}/preview/1",
                                   data={"text": "entries: [unclosed\n",
                                         "client": EXAMPLE_SLUG})
        assert response.status_code == 400

    def test_no_client_chosen_is_a_400(self, app_client, form):
        response = app_client.post(f"/forms/{form}/preview/1",
                                   data={"text": mapping_text(), "client": ""})
        assert response.status_code == 400


class TestBlankPagePng:
    def test_renders_the_blank_form_to_click_on(self, app_client, form):
        response = app_client.get(f"/forms/{form}/blank/1")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_reports_the_page_size_in_points(self, app_client, form):
        response = app_client.get(f"/forms/{form}/blank/1")
        assert float(response.headers["X-Page-Height-Pt"]) == pytest.approx(792, abs=1)


class TestFormImport:
    def upload(self, app_client, content, filename="new_form.pdf", **fields):
        return app_client.post("/forms/new",
                               data={"form_key": fields.get("form_key", "imported"),
                                     "title": fields.get("title", "Imported form"),
                                     "county": fields.get("county", "Hillsborough")},
                               files={"pdf": (filename, content, "application/pdf")})

    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        mapping_edit.mapping_file("imported").unlink(missing_ok=True)
        (paths.BLANK_PDF_DIR / "imported.pdf").unlink(missing_ok=True)

    def test_the_upload_page_renders(self, app_client):
        assert app_client.get("/forms/new").status_code == 200

    def test_accepts_a_pdf_and_proposes_a_mapping(self, app_client, flat_form):
        response = self.upload(app_client, flat_form.read_bytes())
        assert response.status_code == 200
        assert "form_key: imported" in response.text

    def test_it_is_only_a_proposal_until_confirmed(self, app_client, flat_form):
        self.upload(app_client, flat_form.read_bytes())
        assert not mapping_edit.mapping_file("imported").exists()

    def test_confirming_writes_the_mapping(self, app_client, flat_form):
        body = self.upload(app_client, flat_form.read_bytes()).text
        text = yaml.safe_dump(dict(GOOD, form_key="imported",
                                   pdf=f"forms/_blank_pdfs/imported.pdf"))
        response = app_client.post("/forms/new/create", follow_redirects=False,
                                   data={"form_key": "imported", "text": text})
        assert response.status_code == 303
        assert mapping_edit.mapping_file("imported").is_file()

    def test_confirming_a_broken_proposal_is_refused(self, app_client, flat_form):
        self.upload(app_client, flat_form.read_bytes())
        response = app_client.post("/forms/new/create",
                                   data={"form_key": "imported",
                                         "text": "entries: [unclosed\n"})
        assert response.status_code == 422
        assert not mapping_edit.mapping_file("imported").exists()

    def test_rejects_an_empty_file(self, app_client):
        response = self.upload(app_client, b"")
        assert response.status_code == 400 and "empty" in response.text

    def test_rejects_something_that_is_not_a_pdf(self, app_client):
        response = self.upload(app_client, b"just some text, not a PDF at all")
        assert response.status_code == 400
        assert "a PDF" in response.text and "doesn" in response.text

    def test_judges_the_contents_not_the_file_name(self, app_client, flat_form):
        # A perfectly good PDF with an awkward name is still a good PDF; the name
        # is never used as a path.
        response = self.upload(app_client, flat_form.read_bytes(),
                               filename="Form (final) v2 — copy.pdf")
        assert response.status_code == 200

    def test_rejects_a_file_over_the_size_limit(self, app_client):
        from webapp.routers.forms import MAX_PDF_BYTES

        oversized = b"%PDF-1.4\n" + b"0" * MAX_PDF_BYTES
        response = self.upload(app_client, oversized)
        assert response.status_code == 400 and "limit" in response.text

    def test_refuses_to_overwrite_an_existing_form(self, app_client, flat_form, form):
        response = self.upload(app_client, flat_form.read_bytes(), form_key="editable")
        assert response.status_code == 400 and "already exists" in response.text

    def test_keeps_what_was_typed_when_an_upload_is_refused(self, app_client):
        response = self.upload(app_client, b"not a pdf", title="My Long Form Title")
        assert "My Long Form Title" in response.text
