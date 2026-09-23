"""The routes: creating, editing, generating, deleting — through the real app."""
from __future__ import annotations

import pytest

import paths
from client_context import client_path, load_client, output_dir, save_client

NEW_CLIENT_FORM = {
    "case[display_name]": "Doe, Jane",
    "case[case_number]": "QV-1",
    "case[acting_as_agent]": "1",
    "case[agent_name]": "A. Agent",
    "case[agent_email]": "agent@example.invalid",
    "case[exemption][category]": "military",
    "case[exemption][code]": "",
    "case[exemption][title]": "",
    "case[exemption][cite]": "",
    "case[exemption][job_title]": "Staff Sergeant",
    "case[exemption][employing_agency]": "U.S. Army",
    "persons[0][role]": "primary",
    "persons[0][first_name]": "Jane",
    "persons[0][middle_name]": "",
    "persons[0][last_name]": "Doe",
    "persons[0][suffix]": "",
    "persons[0][dob]": "1990-01-01",
    "persons[0][phones][]": "813-555-0100",
    "persons[0][emails][]": "jane@example.invalid",
    "persons[0][residence_index]": "0",
    "persons[0][addresses][0][line1]": "1 Main St",
    "persons[0][addresses][0][line2]": "",
    "persons[0][addresses][0][city]": "Tampa",
    "persons[0][addresses][0][county]": "Hillsborough",
    "persons[0][addresses][0][state]": "FL",
    "persons[0][addresses][0][zip]": "33602",
    "persons[0][addresses][0][parcel_id]": "",
    "persons[0][addresses][0][current]": "1",
}


class TestClientList:
    def test_shows_the_empty_state_first(self, app_client):
        assert "No clients yet" in app_client.get("/").text

    def test_lists_a_saved_client(self, app_client, example_client):
        save_client("Listed", example_client)
        assert "Example, Alex" in app_client.get("/").text

    def test_never_loses_the_human_gate_notice(self, app_client):
        # "prepares paperwork only" is a promise the UI makes on every visit.
        assert "prepares paperwork only" in app_client.get("/").text


class TestCreate:
    def test_writes_a_client_file(self, app_client):
        response = app_client.post("/clients/new", data=NEW_CLIENT_FORM,
                                   follow_redirects=False)
        assert response.status_code == 303
        assert client_path("doe_jane").is_file()

    def test_the_saved_file_is_an_ordinary_client_yaml(self, app_client):
        app_client.post("/clients/new", data=NEW_CLIENT_FORM, follow_redirects=False)
        client = load_client(client_path("doe_jane"))
        assert client["case"]["display_name"] == "Doe, Jane"
        assert client["persons"][0]["addresses"][0]["residence"] is True

    def test_rejects_an_incomplete_client_without_writing(self, app_client):
        broken = dict(NEW_CLIENT_FORM, **{"case[display_name]": ""})
        response = app_client.post("/clients/new", data=broken)
        assert response.status_code == 422
        assert not list(paths.CLIENTS_DIR.glob("*.yaml"))

    def test_says_what_is_wrong(self, app_client):
        broken = dict(NEW_CLIENT_FORM, **{"persons[0][dob]": ""})
        assert "date of birth is required" in app_client.post(
            "/clients/new", data=broken).text

    def test_refuses_to_overwrite_an_existing_client(self, app_client):
        app_client.post("/clients/new", data=NEW_CLIENT_FORM, follow_redirects=False)
        response = app_client.post("/clients/new", data=NEW_CLIENT_FORM)
        assert response.status_code == 422
        assert "already exists" in response.text


class TestEdit:
    def test_renders_the_saved_values(self, app_client, example_client):
        save_client("Editable", example_client)
        assert "Alexandra" in app_client.get("/clients/Editable/edit").text

    def test_saving_keeps_keys_the_form_does_not_manage(self, app_client,
                                                        example_client):
        import documents as D

        client = D.set_documents(example_client, [{"instrument_number": "2023111222",
                                                   "title": "(D) DEED"}])
        client["marks"] = {"some_form": [{"entry": 1, "on": True}]}
        save_client("Editable", client)

        app_client.post("/clients/Editable/edit", data=NEW_CLIENT_FORM,
                        follow_redirects=False)

        after = load_client(client_path("Editable"))
        assert after["documents"][0]["instrument_number"] == "2023111222"
        assert after["marks"]["some_form"]

    def test_the_file_name_stays_put_when_the_display_name_changes(
            self, app_client, example_client):
        save_client("Editable", example_client)
        app_client.post("/clients/Editable/edit", data=NEW_CLIENT_FORM,
                        follow_redirects=False)
        assert client_path("Editable").is_file()
        assert not client_path("doe_jane").exists()

    def test_editing_something_that_does_not_exist_is_a_404(self, app_client):
        assert app_client.post("/clients/Nope/edit", data=NEW_CLIENT_FORM,
                               follow_redirects=False).status_code == 404


class TestDelete:
    def test_the_confirmation_page_lists_what_would_go(self, app_client,
                                                       example_client):
        save_client("Doomed", example_client)
        out = output_dir(load_client(client_path("Doomed")))
        out.mkdir(parents=True, exist_ok=True)
        (out / "a_form.pdf").write_bytes(b"%PDF-1.4\n")

        body = app_client.get("/clients/Doomed/delete").text
        assert "a_form.pdf" in body

    def test_a_get_never_deletes(self, app_client, example_client):
        save_client("Doomed", example_client)
        app_client.get("/clients/Doomed/delete")
        assert client_path("Doomed").is_file()

    def test_a_post_without_confirmation_is_refused(self, app_client, example_client):
        save_client("Doomed", example_client)
        response = app_client.post("/clients/Doomed/delete", data={})
        assert response.status_code == 400
        assert client_path("Doomed").is_file()

    def test_a_confirmed_delete_moves_it_to_trash(self, app_client, example_client):
        save_client("Doomed", example_client)
        response = app_client.post("/clients/Doomed/delete", data={"confirm": "yes"},
                                   follow_redirects=False)
        assert response.status_code == 303
        assert not client_path("Doomed").exists()
        assert list(paths.TRASH_DIR.glob("*__doomed/Doomed.yaml"))


class TestSlugGuards:
    @pytest.mark.parametrize("slug", ["../secrets", "..", "_TEMPLATE", "a/b",
                                      ".hidden", ""])
    def test_a_crafted_slug_is_a_404_not_a_file_read(self, app_client, slug):
        assert app_client.get(f"/clients/{slug}/edit").status_code == 404

    @pytest.mark.parametrize("filename", ["../../paths.py", "notes.txt", "a/b.pdf"])
    def test_a_crafted_output_filename_is_a_404(self, app_client, filename,
                                                example_client):
        save_client("Guarded", example_client)
        assert app_client.get(
            f"/clients/Guarded/download/{filename}").status_code == 404


class TestGenerate:
    def test_the_picker_lists_people_and_forms(self, app_client, example_client):
        save_client("Generating", example_client)
        body = app_client.get("/clients/Generating/generate").text
        assert "primary" in body

    def test_blocks_generation_while_the_client_has_errors(self, app_client,
                                                           example_client):
        example_client["persons"][0]["dob"] = ""
        save_client("Incomplete", example_client)
        response = app_client.post("/clients/Incomplete/generate", data={})
        assert response.status_code == 422
        assert not output_dir(load_client(client_path("Incomplete"))).exists()

    def test_generates_into_the_client_s_own_folder(self, app_client, example_client):
        save_client("Generating", example_client)
        response = app_client.post("/clients/Generating/generate",
                                   data={"forms[]": "demo_redaction"})
        assert response.status_code == 200
        assert (output_dir(load_client(client_path("Generating")))
                / "demo_redaction__primary.pdf").is_file()

    def test_an_engine_failure_reports_instead_of_a_bare_500(self, app_client,
                                                              example_client,
                                                              monkeypatch):
        import webapp.routers.generate as route

        save_client("Generating", example_client)

        def boom(*a, **k):
            raise OSError("the blank form is open in another program")

        monkeypatch.setattr(route, "fill_client", boom)
        response = app_client.post("/clients/Generating/generate",
                                   data={"forms[]": "demo_redaction"})
        assert response.status_code == 500
        assert "Generation stopped part-way" in response.text
        assert "another program" in response.text       # names the actual problem

    def test_a_partial_failure_still_lists_what_was_written(self, app_client,
                                                             example_client,
                                                             monkeypatch):
        import webapp.routers.generate as route

        save_client("Generating", example_client)
        # One form succeeds, then the run dies.
        app_client.post("/clients/Generating/generate",
                        data={"forms[]": "demo_redaction"})

        def boom(*a, **k):
            raise RuntimeError("stopped")

        monkeypatch.setattr(route, "fill_client", boom)
        body = app_client.post("/clients/Generating/generate",
                               data={"forms[]": "demo_redaction"}).text
        # The worker can see which file exists rather than guessing.
        assert "demo_redaction__primary.pdf" in body

    def test_lists_what_was_generated(self, app_client, example_client):
        save_client("Generating", example_client)
        app_client.post("/clients/Generating/generate",
                        data={"forms[]": "demo_redaction"})
        assert "demo_redaction" in app_client.get("/clients/Generating/files").text


class TestWorklist:
    def test_the_page_shows_the_catalog_read_only(self, app_client, example_client):
        save_client("Worklisted", example_client)
        body = app_client.get("/clients/Worklisted/worklist").text
        assert "Prepared, not submitted" in body

    def test_building_writes_the_spreadsheet(self, app_client, example_client):
        save_client("Worklisted", example_client)
        response = app_client.post("/clients/Worklisted/worklist", data={})
        assert response.status_code == 200
        assert (output_dir(load_client(client_path("Worklisted")))
                / "opt_out_worklist.xlsx").is_file()

    def test_rebuilding_warns_that_it_keeps_tracked_work(self, app_client,
                                                         example_client):
        save_client("Worklisted", example_client)
        app_client.post("/clients/Worklisted/worklist", data={})
        body = app_client.get("/clients/Worklisted/worklist").text
        assert "Rebuilding keeps everything you've filled in" in body


class TestFormsOverview:
    def test_lists_the_mappings_read_only(self, app_client):
        body = app_client.get("/forms").text
        assert "hillsborough_clerk_redaction" in body


class TestErrorPage:
    def test_a_404_renders_as_a_page_not_raw_json(self, app_client):
        response = app_client.get("/clients/Nope/edit")
        assert response.status_code == 404
        assert "<!DOCTYPE html>" in response.text
