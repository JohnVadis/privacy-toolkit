"""Turning posted form data into a client file, and back."""
from __future__ import annotations

import pytest

from webapp import model
from webapp.formparse import parse_nested, split_key


class TestSplitKey:
    @pytest.mark.parametrize("key,expected", [
        ("case[display_name]", ["case", "display_name"]),
        ("persons[0][dob]", ["persons", "0", "dob"]),
        ("persons[0][addresses][1][city]", ["persons", "0", "addresses", "1", "city"]),
        ("persons[0][phones][]", ["persons", "0", "phones", ""]),
        ("plain", ["plain"]),
    ])
    def test_splits_the_bracket_convention(self, key, expected):
        assert split_key(key) == expected


class TestParseNested:
    def test_builds_nested_dicts(self):
        assert parse_nested([("case[display_name]", "Doe, Jane")]) == {
            "case": {"display_name": "Doe, Jane"}}

    def test_turns_numeric_keys_into_lists(self):
        data = parse_nested([("persons[0][dob]", "a"), ("persons[1][dob]", "b")])
        assert [p["dob"] for p in data["persons"]] == ["a", "b"]

    def test_closes_gaps_left_by_a_removed_row(self):
        # Removing the middle of 0,1,2 leaves 0,2 — the browser does not renumber.
        data = parse_nested([("persons[0][dob]", "a"), ("persons[2][dob]", "c")])
        assert [p["dob"] for p in data["persons"]] == ["a", "c"]

    def test_appends_in_document_order(self):
        data = parse_nested([("p[phones][]", "1"), ("p[phones][]", "2")])
        assert data["p"]["phones"] == ["1", "2"]

    def test_last_value_wins_which_is_what_makes_checkboxes_work(self):
        # An unchecked box posts only the hidden "0"; a checked one posts "0" then "1".
        assert parse_nested([("on", "0"), ("on", "1")])["on"] == "1"
        assert parse_nested([("on", "0")])["on"] == "0"

    def test_ignores_an_empty_key(self):
        assert parse_nested([("", "x")]) == {}


class TestClientFromForm:
    def form(self, **overrides):
        data = {
            "case": {"display_name": "Doe, Jane", "case_number": "QV-1",
                     "acting_as_agent": "1", "agent_name": "A", "agent_email": "a@b.c",
                     "exemption": {"category": "military", "code": "", "title": "",
                                   "cite": "", "job_title": "", "employing_agency": ""}},
            "persons": [{
                "role": "primary", "first_name": "Jane", "last_name": "Doe",
                "dob": "1990-01-01", "phones": ["813-555-0100"], "emails": ["j@d.invalid"],
                "residence_index": "0",
                "addresses": [{"line1": "1 Main St", "city": "Tampa",
                               "county": "Hillsborough", "state": "FL", "zip": "33602"}],
            }],
        }
        data.update(overrides)
        return data

    def test_produces_the_template_shape(self):
        client = model.client_from_form(self.form())
        assert list(client) == ["case", "persons"]
        assert client["case"]["display_name"] == "Doe, Jane"

    def test_trims_whitespace(self):
        data = self.form()
        data["case"]["display_name"] = "  Doe, Jane  "
        assert model.client_from_form(data)["case"]["display_name"] == "Doe, Jane"

    def test_drops_blank_repeat_rows(self):
        data = self.form()
        data["persons"][0]["phones"] = ["813-555-0100", "", "  "]
        assert model.client_from_form(data)["persons"][0]["phones"] == ["813-555-0100"]

    def test_drops_an_address_with_nothing_in_it(self):
        data = self.form()
        data["persons"][0]["addresses"].append({"line1": "", "city": ""})
        assert len(model.client_from_form(data)["persons"][0]["addresses"]) == 1

    def test_marks_exactly_one_residence_from_the_radio_group(self):
        data = self.form()
        data["persons"][0]["addresses"].append(
            {"line1": "2 Other St", "city": "Brandon"})
        data["persons"][0]["residence_index"] = "1"
        addresses = model.client_from_form(data)["persons"][0]["addresses"]
        assert [a["residence"] for a in addresses] == [False, True]

    def test_falls_back_to_the_first_address_if_the_chosen_one_was_blank(self):
        data = self.form()
        data["persons"][0]["residence_index"] = "5"      # points at nothing
        addresses = model.client_from_form(data)["persons"][0]["addresses"]
        assert addresses[0]["residence"] is True

    def test_reads_a_checkbox(self):
        data = self.form()
        data["case"]["acting_as_agent"] = "0"
        assert model.client_from_form(data)["case"]["acting_as_agent"] is False


class TestPreserveUnmanaged:
    """The edit form rebuilds the client from scratch; these keys aren't on it."""

    @pytest.mark.parametrize("key", model.UNMANAGED_KEYS)
    def test_each_unmanaged_key_survives_a_save(self, key):
        existing = {"case": {}, "persons": [], key: [{"anything": True}]}
        merged = model.preserve_unmanaged(model.blank_client(), existing)
        assert merged[key] == [{"anything": True}]

    def test_documents_are_among_them(self):
        # Imported record lists are not fields on the edit form; without this a
        # single Save would wipe the documents the Clerk form names.
        assert "documents" in model.UNMANAGED_KEYS

    def test_nothing_is_invented_when_there_is_nothing_to_keep(self):
        merged = model.preserve_unmanaged(model.blank_client(), {"case": {}})
        assert not any(k in merged for k in model.UNMANAGED_KEYS)


class TestSuggestSlug:
    @pytest.mark.parametrize("display,expected", [
        ("Doe, Jane", "doe_jane"),
        ("", "client"),
    ])
    def test_derives_a_filename_stem(self, display, expected):
        assert model.suggest_slug({"case": {"display_name": display}}) == expected


def test_blank_client_has_one_primary_person():
    client = model.blank_client()
    assert len(client["persons"]) == 1
    assert client["persons"][0]["role"] == "primary"
