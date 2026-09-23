"""Fixing a generated form by hand — and the two guards that keep those fixes honest.

A hand edit is stored against the CLIENT, not the produced PDF, so it is re-applied
every time the form is generated. That makes it durable, and it makes it dangerous:
the mapping can move underneath it. Both stored kinds carry something to re-check.
"""
from __future__ import annotations

import pytest
import yaml

import fill_forms as FF
from client_context import client_path, load_client, save_client


@pytest.fixture
def mapping(tmp_path, flat_form, monkeypatch):
    """A two-entry overlay mapping installed where the app will find it."""
    import paths

    data = {
        "mode": "overlay", "form_key": "editable", "title": "Editable test form",
        "pdf": str(flat_form),
        "entries": [
            {"page": 1, "x": 160, "y": 700, "from": "full_name"},
            {"page": 1, "x": 72, "y": 580, "mark": "X",
             "when": {"field": "exemption_category", "equals": "judges"}},
            {"page": 1, "x": 200, "y": 580, "mark": "X",
             "when": {"field": "exemption_category", "equals": "military"}},
        ],
    }
    path = paths.MAPPINGS_DIR / "editable.map.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def generated(app_client, example_client, mapping):
    """A client with that form already generated, ready to be corrected."""
    save_client("Correcting", example_client)
    app_client.post("/clients/Correcting/generate", data={"forms[]": "editable"})
    return "Correcting", "editable__primary.pdf"


def saved(slug):
    return load_client(client_path(slug))


class TestEditText:
    def test_the_editor_shows_the_current_values(self, app_client, generated):
        slug, filename = generated
        body = app_client.get(f"/clients/{slug}/text/{filename}").text
        assert "Alexandra M Example" in body

    def test_an_edited_value_is_stored_as_an_override(self, app_client, generated):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"rows[0][entry]": "0", "rows[0][kind]": "text",
                              "rows[0][value]": "TYPED BY HAND",
                              "rows[0][original]": "Alexandra M Example",
                              "rows[0][from]": "full_name"})
        override = saved(slug)["overrides"]["editable"][0]
        assert override["text"] == "TYPED BY HAND"

    def test_it_remembers_which_field_it_belonged_to(self, app_client, generated):
        # Without `from`, a later mapping edit would move this onto another field.
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"rows[0][entry]": "0", "rows[0][kind]": "text",
                              "rows[0][value]": "TYPED BY HAND",
                              "rows[0][original]": "Alexandra M Example",
                              "rows[0][from]": "full_name"})
        assert saved(slug)["overrides"]["editable"][0]["from"] == "full_name"

    def test_an_unchanged_value_is_not_stored(self, app_client, generated):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"rows[0][entry]": "0", "rows[0][kind]": "text",
                              "rows[0][value]": "Alexandra M Example",
                              "rows[0][original]": "Alexandra M Example",
                              "rows[0][from]": "full_name"})
        # The mapping keeps driving anything the worker didn't actually change.
        assert not saved(slug).get("overrides", {}).get("editable")

    def test_the_stored_override_reaches_the_regenerated_form(self, app_client,
                                                              generated, mapping):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"rows[0][entry]": "0", "rows[0][kind]": "text",
                              "rows[0][value]": "TYPED BY HAND",
                              "rows[0][original]": "Alexandra M Example",
                              "rows[0][from]": "full_name"})

        import pdfplumber

        out = FF.fill_one(mapping, saved(slug), None, report=lambda *_: None)
        with pdfplumber.open(out) as pdf:
            assert "TYPED BY HAND" in (pdf.pages[0].extract_text() or "")


class TestTickBoxes:
    def _post_tick(self, app_client, slug, filename, on, by_rule):
        return app_client.post(
            f"/clients/{slug}/text/{filename}", follow_redirects=False,
            data={"rows[0][entry]": "1", "rows[0][kind]": "mark",
                  "rows[0][on]": "1" if on else "0",
                  "rows[0][by_rule]": "1" if by_rule else "0"})

    def test_a_tick_that_disagrees_with_the_mapping_is_stored(self, app_client,
                                                              generated):
        slug, filename = generated
        self._post_tick(app_client, slug, filename, on=True, by_rule=False)
        assert saved(slug)["marks"]["editable"][0]["on"] is True

    def test_a_tick_that_agrees_with_the_mapping_is_not_stored(self, app_client,
                                                               generated):
        slug, filename = generated
        self._post_tick(app_client, slug, filename, on=False, by_rule=False)
        assert not saved(slug).get("marks", {}).get("editable")

    def test_the_stored_tick_records_where_it_lands(self, app_client, generated,
                                                    mapping):
        # This is the drift guard: an index alone would silently re-point at a
        # different box the moment anyone inserts an entry above it.
        slug, filename = generated
        self._post_tick(app_client, slug, filename, on=True, by_rule=False)

        entry = FF.load_mapping(mapping)["entries"][1]
        assert saved(slug)["marks"]["editable"][0]["at"] == FF.mark_signature(entry)

    def test_a_tick_survives_regeneration(self, app_client, generated, mapping):
        slug, filename = generated
        self._post_tick(app_client, slug, filename, on=True, by_rule=False)

        marks = self._marks_after_fill(mapping, saved(slug))
        # The military box by rule, plus the judges box ticked by hand.
        assert len(marks) == 2

    def _marks_after_fill(self, mapping, client, report=None):
        import pdfplumber

        out = FF.fill_one(mapping, client, None,
                          report=report or (lambda *_: None))
        with pdfplumber.open(out) as pdf:
            return [w for w in pdf.pages[0].extract_words()
                    if w["text"].strip().endswith("X") and len(w["text"].strip()) <= 2]

    def test_a_tick_is_dropped_once_another_box_takes_its_index(self, app_client,
                                                                generated, mapping):
        slug, filename = generated
        self._post_tick(app_client, slug, filename, on=True, by_rule=False)

        # Reorder the two tick boxes: index 1 is now the OTHER box entirely.
        data = yaml.safe_load(mapping.read_text(encoding="utf-8"))
        data["entries"][1], data["entries"][2] = data["entries"][2], data["entries"][1]
        mapping.write_text(yaml.safe_dump(data), encoding="utf-8")

        notes = []
        marks = self._marks_after_fill(mapping, saved(slug), report=notes.append)
        assert any("no longer matches" in n for n in notes)
        # It fell back to the mapping's rule rather than ticking the wrong box:
        # military only, not judges.
        assert len(marks) == 1
        assert marks[0]["x0"] == pytest.approx(200, abs=3)

    def test_a_tick_is_ignored_when_its_index_is_no_longer_a_box_at_all(
            self, app_client, generated, mapping):
        slug, filename = generated
        self._post_tick(app_client, slug, filename, on=True, by_rule=False)

        # Insert a line above: index 1 is now a TEXT entry, so the stored tick is
        # never consulted. Silent, but it cannot mark the wrong box either.
        data = yaml.safe_load(mapping.read_text(encoding="utf-8"))
        data["entries"].insert(0, {"page": 1, "x": 300, "y": 300,
                                   "literal": "newly added"})
        mapping.write_text(yaml.safe_dump(data), encoding="utf-8")

        marks = self._marks_after_fill(mapping, saved(slug))
        assert len(marks) == 1          # military by rule; nothing ticked by hand


class TestAddedCorrections:
    def test_text_added_on_a_blank_spot_is_stored(self, app_client, generated):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"added[0][page]": "1", "added[0][x]": "100",
                              "added[0][y]": "400", "added[0][text]": "extra note",
                              "added[0][kind]": "text"})
        extra = saved(slug)["corrections"]["editable"][0]
        assert extra["text"] == "extra note" and extra["x"] == 100

    def test_a_mark_added_on_a_blank_spot_is_stored(self, app_client, generated):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"added[0][page]": "1", "added[0][x]": "100",
                              "added[0][y]": "400", "added[0][kind]": "mark"})
        assert saved(slug)["corrections"]["editable"][0]["mark"] == "X"

    def test_an_addition_with_no_coordinates_is_skipped(self, app_client, generated):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"added[0][text]": "nowhere", "added[0][kind]": "text"})
        assert not saved(slug).get("corrections", {}).get("editable")

    def test_an_empty_addition_is_skipped(self, app_client, generated):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"added[0][page]": "1", "added[0][x]": "100",
                              "added[0][y]": "400", "added[0][text]": "   ",
                              "added[0][kind]": "text"})
        assert not saved(slug).get("corrections", {}).get("editable")

    def test_corrections_reach_the_regenerated_form(self, app_client, generated,
                                                    mapping):
        slug, filename = generated
        app_client.post(f"/clients/{slug}/text/{filename}", follow_redirects=False,
                        data={"added[0][page]": "1", "added[0][x]": "100",
                              "added[0][y]": "400", "added[0][text]": "stamped later",
                              "added[0][kind]": "text"})

        import pdfplumber

        out = FF.fill_one(mapping, saved(slug), None, report=lambda *_: None)
        with pdfplumber.open(out) as pdf:
            assert "stamped later" in (pdf.pages[0].extract_text() or "")


class TestGuards:
    def test_a_crafted_filename_is_a_404(self, app_client, example_client):
        save_client("Guarded", example_client)
        assert app_client.get(
            "/clients/Guarded/text/../../paths.py").status_code == 404

    def test_a_page_png_of_something_that_is_not_generated_is_a_404(self, app_client,
                                                                    example_client):
        save_client("Guarded", example_client)
        assert app_client.get(
            "/clients/Guarded/pagepng/nothing.pdf/1").status_code == 404
