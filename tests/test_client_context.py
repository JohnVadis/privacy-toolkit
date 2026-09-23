"""The client file: what it flattens into, and how it is written and deleted."""
from __future__ import annotations

import datetime

import pytest

import paths
from client_context import (CLIENT_BACKUP_DIR, SLUG_KEY, ClientDataError,
                            _fmt_date, build_context, client_path, client_slug,
                            delete_client, deletion_preview, dump_client,
                            list_clients, load_client, output_dir, person_roles,
                            save_client)


class TestBuildContext:
    def test_joins_the_name_parts_that_are_present(self, example_client):
        ctx = build_context(example_client)
        assert ctx["full_name"] == "Alexandra M Example"

    def test_skips_a_missing_middle_name_without_doubling_the_space(self):
        client = {"case": {}, "persons": [{"role": "primary", "first_name": "Ann",
                                           "last_name": "Lee"}]}
        assert build_context(client)["full_name"] == "Ann Lee"

    def test_home_full_is_one_line_with_the_zip_attached(self, example_client):
        ctx = build_context(example_client)
        assert ctx["home_full"] == (
            "1420 Sample Ridge Drive, Apt 21B, Brandon, FL 33511")

    def test_picks_the_address_marked_as_residence(self):
        client = {"case": {}, "persons": [{"role": "primary", "addresses": [
            {"line1": "Old St", "city": "Tampa", "residence": False},
            {"line1": "New St", "city": "Brandon", "residence": True},
        ]}]}
        assert build_context(client)["home_line1"] == "New St"

    def test_falls_back_to_the_first_address_when_none_is_marked(self):
        client = {"case": {}, "persons": [{"role": "primary", "addresses": [
            {"line1": "Only St"}]}]}
        assert build_context(client)["home_line1"] == "Only St"

    def test_everything_is_empty_rather_than_missing_for_a_bare_person(self):
        client = {"case": {}, "persons": [{"role": "primary"}]}
        ctx = build_context(client)
        # A mapping reads these blind; None would stamp "None" onto a filed form.
        for key in ("phone", "email", "home_full", "home_county", "dob"):
            assert ctx[key] == ""

    @pytest.mark.parametrize("role,expected", [
        ("primary", "Self"), ("spouse", "Spouse"), ("child", "Dependent"),
    ])
    def test_owner_relationship_follows_the_role(self, role, expected):
        client = {"case": {}, "persons": [{"role": role, "first_name": "A",
                                           "last_name": "B"}]}
        assert build_context(client)["owner_relationship"] == f"A B / {expected}"

    def test_selects_a_person_by_role(self, example_client):
        example_client["persons"].append({"role": "spouse", "first_name": "Sam",
                                          "last_name": "Example"})
        assert build_context(example_client, "spouse")["first_name"] == "Sam"

    def test_unknown_role_is_an_error_not_a_silent_default(self, example_client):
        with pytest.raises(ClientDataError):
            build_context(example_client, "cousin")

    def test_no_persons_is_an_error(self):
        with pytest.raises(ClientDataError):
            build_context({"case": {}, "persons": []})


class TestFormatDate:
    @pytest.mark.parametrize("value,expected", [
        ("2002-12-29", "12/29/2002"),
        (datetime.date(2002, 12, 29), "12/29/2002"),
        ("12/29/2002", "12/29/2002"),
        ("", ""),
        (None, ""),
    ])
    def test_renders_mm_dd_yyyy(self, value, expected):
        assert _fmt_date(value) == expected

    def test_passes_an_unparseable_value_through_unchanged(self):
        # Documented behaviour, and a known sharp edge: a d-m-y string is stamped
        # onto the form exactly as typed rather than being reinterpreted.
        assert _fmt_date("29-12-2002") == "29-12-2002"


class TestSaveAndLoad:
    def test_round_trips_without_changing_types(self, example_client):
        save_client("Round Trip", example_client)
        loaded = load_client(client_path("Round Trip"))
        assert loaded["persons"][0]["dob"] == "2002-04-17" or isinstance(
            loaded["persons"][0]["dob"], str)
        assert loaded["persons"][0]["addresses"][0]["zip"] == "33511"  # still a string

    def test_records_which_file_it_came_from(self, example_client):
        save_client("Identity", example_client)
        assert client_slug(load_client(client_path("Identity"))) == "Identity"

    def test_but_never_writes_that_back_into_the_file(self, example_client):
        save_client("Identity", example_client)
        assert SLUG_KEY not in client_path("Identity").read_text(encoding="utf-8")

    def test_dump_drops_runtime_keys(self):
        assert SLUG_KEY not in dump_client({"case": {}, SLUG_KEY: "x"})

    def test_backs_up_the_previous_version_on_every_save(self, example_client):
        save_client("Backed Up", example_client)
        example_client["case"]["case_number"] = "CHANGED-AFTER-BACKUP"
        save_client("Backed Up", example_client)

        backups = list((CLIENT_BACKUP_DIR / "Backed Up").glob("*.yaml"))
        assert len(backups) == 1
        # The backup holds what was there BEFORE the second save.
        assert "CHANGED-AFTER-BACKUP" not in backups[0].read_text(encoding="utf-8")
        assert "CHANGED-AFTER-BACKUP" in client_path("Backed Up").read_text(
            encoding="utf-8")

    def test_a_first_save_has_nothing_to_back_up(self, example_client):
        save_client("Brand New", example_client)
        assert not (CLIENT_BACKUP_DIR / "Brand New").exists()

    def test_leaves_the_old_file_intact_when_serialization_fails(self, example_client):
        save_client("Fragile", example_client)
        before = client_path("Fragile").read_text(encoding="utf-8")

        class Unserializable:
            pass

        with pytest.raises(Exception):
            save_client("Fragile", {"case": {"display_name": Unserializable()}})
        assert client_path("Fragile").read_text(encoding="utf-8") == before

    def test_leaves_no_temp_or_lock_files_behind(self, example_client):
        save_client("Tidy", example_client)
        assert not list(paths.CLIENTS_DIR.glob("*.tmp"))
        assert not list(paths.CLIENTS_DIR.glob("*.lock"))

    def test_list_clients_skips_the_template_and_reports_the_output_folder(
            self, example_client):
        save_client("Listed", example_client)
        rows = {r["slug"]: r for r in list_clients()}
        assert "_TEMPLATE" not in rows
        assert rows["Listed"]["output_slug"] == "Listed"

    def test_list_clients_reports_a_broken_file_instead_of_crashing(self):
        (paths.CLIENTS_DIR / "Broken.yaml").write_text("case: [unclosed\n",
                                                       encoding="utf-8")
        rows = {r["slug"]: r for r in list_clients()}
        assert rows["Broken"]["error"]


class TestOutputDir:
    def test_two_clients_sharing_a_display_name_get_different_folders(self):
        a = {"case": {"display_name": "Smith, John"}, SLUG_KEY: "Smith John"}
        b = {"case": {"display_name": "Smith, John"}, SLUG_KEY: "Smith John 2"}
        assert output_dir(a) != output_dir(b)

    def test_is_keyed_on_the_file_not_the_display_name(self):
        client = {"case": {"display_name": "Anything At All"}, SLUG_KEY: "Stable"}
        assert output_dir(client).name == "Stable"

    def test_adopts_a_folder_left_by_the_old_display_name_scheme(self):
        legacy = paths.OUTPUT_DIR / "smith_john"
        legacy.mkdir(parents=True)
        client = {"case": {"display_name": "Smith, John"}, SLUG_KEY: "Smith John"}
        assert output_dir(client) == legacy      # nothing has to be migrated

    def test_prefers_the_new_folder_once_it_exists(self):
        (paths.OUTPUT_DIR / "smith_john").mkdir(parents=True)
        (paths.OUTPUT_DIR / "Smith John").mkdir(parents=True)
        client = {"case": {"display_name": "Smith, John"}, SLUG_KEY: "Smith John"}
        assert output_dir(client).name == "Smith John"

    def test_an_in_memory_client_with_no_file_still_lands_somewhere_safe(self):
        d = output_dir({"case": {"display_name": "_mapping preview"}})
        assert d.resolve() != paths.OUTPUT_DIR.resolve()

    def test_a_nonsense_display_name_never_resolves_to_the_output_root(self):
        d = output_dir({"case": {"display_name": "..."}})
        assert d.resolve() != paths.OUTPUT_DIR.resolve()


class TestDeletion:
    def test_preview_reports_the_file_and_its_output(self, example_client):
        save_client("Doomed", example_client)
        out = output_dir(load_client(client_path("Doomed")))
        out.mkdir(parents=True, exist_ok=True)
        (out / "form.pdf").write_bytes(b"%PDF-1.4\n")

        preview = deletion_preview("Doomed")
        assert preview["client_file_exists"]
        assert preview["generated"] == ["form.pdf"]

    def test_preview_never_offers_the_whole_output_tree(self, example_client):
        example_client["case"]["display_name"] = "..."      # slugifies to nothing
        save_client("Nonsense", example_client)
        preview = deletion_preview("Nonsense")
        assert preview["output_dir"] != paths.OUTPUT_DIR

    def test_deleting_one_client_leaves_another_alone(self, example_client):
        import copy

        save_client("Keeper", example_client)
        other = copy.deepcopy(example_client)
        other["case"]["display_name"] = "Example, Alex"      # same display name!
        save_client("Goner", other)

        for slug in ("Keeper", "Goner"):
            d = output_dir(load_client(client_path(slug)))
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{slug}.pdf").write_bytes(b"%PDF-1.4\n")

        delete_client("Goner")

        assert client_path("Keeper").is_file()
        assert (output_dir(load_client(client_path("Keeper"))) / "Keeper.pdf").is_file()

    def test_moves_rather_than_unlinks(self, example_client):
        save_client("Recoverable", example_client)
        dest = delete_client("Recoverable")
        assert not client_path("Recoverable").exists()
        assert (dest / "Recoverable.yaml").is_file()

    def test_deleting_something_that_is_not_there_is_an_error(self):
        with pytest.raises(ClientDataError):
            delete_client("Never Existed")


def test_person_roles_lists_them_in_file_order(example_client):
    example_client["persons"].append({"role": "spouse"})
    assert person_roles(example_client) == ["primary", "spouse"]
