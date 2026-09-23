"""The Documents screen: import, pick the rows that are the client's, edit, remove."""
from __future__ import annotations

import re

import pytest

import documents as D
from client_context import client_path, load_client, save_client

EXPORT = (
    "Instrument,Grantor,Grantee,RecordDate,DocType,BookType,BookNum,PageNum,Legal,SalesPrice\n"
    "2023111222,DUALITY NATURE INC,\"MARLOWE JOHN P, OKONKWO PRIYA R\","
    "2023-08-14 09:00 AM,(D) DEED,,,,PLANT CITY,\n"
    "2019123456,A CORP,SMITH JOHN,2019-05-14 11:28 AM,(D) WARRANTY DEED,O,26841,1234,,\n"
    "2021998877,B CORP,JONES MARY,2021-01-02 08:00 AM,(SAT) SATISFACTION,O,28002,455,,\n"
)

PLAIN = ("Instrument Number,Book,Page,Document Title\n"
         "2019123456,26841,1234,WARRANTY DEED\n")


@pytest.fixture
def slug(example_client):
    save_client("Docs", example_client)
    return "Docs"


def stored(slug):
    return D.documents(load_client(client_path(slug)))


def pick_values(html):
    return re.findall(r'name="pick" value="(\d+)"', html)


class TestPage:
    def test_shows_the_empty_state(self, app_client, slug):
        body = app_client.get(f"/clients/{slug}/documents").text
        assert "Nothing imported yet" in body

    def test_says_the_table_will_print_blank(self, app_client, slug):
        body = app_client.get(f"/clients/{slug}/documents").text
        assert "document table prints blank" in body

    def test_says_how_many_rows_each_form_holds(self, app_client, slug):
        body = app_client.get(f"/clients/{slug}/documents").text
        assert "4 redact row" in body and "Attachment A" in body


class TestImportPreview:
    def test_offers_every_parsed_row(self, app_client, slug):
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": EXPORT, "list": "redact"}).text
        assert len(pick_values(body)) == 3

    def test_nothing_starts_ticked(self, app_client, slug):
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": EXPORT, "list": "redact"}).text
        boxes = re.findall(r'<input type="checkbox" name="pick"[^>]*>', body)
        assert boxes and not any("checked" in b for b in boxes)

    def test_saves_nothing_yet(self, app_client, slug):
        app_client.post(f"/clients/{slug}/documents/import",
                        data={"text": EXPORT, "list": "redact"})
        assert stored(slug) == []

    def test_shows_the_party_names_so_a_person_can_choose(self, app_client, slug):
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": EXPORT, "list": "redact"}).text
        assert "OKONKWO PRIYA R" in body

    def test_warns_that_a_name_search_is_not_a_person(self, app_client, slug):
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": EXPORT, "list": "redact"}).text
        assert "matches a name, not a person" in body

    def test_reports_the_columns_it_ignored(self, app_client, slug):
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": EXPORT, "list": "redact"}).text
        assert "BookType" in body

    def test_refuses_a_table_that_is_not_a_record_list(self, app_client, slug):
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": "name,city\nAlex,Tampa", "list": "redact"}).text
        assert "Nothing was imported" in body

    def test_refuses_an_empty_paste(self, app_client, slug):
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": "", "list": "redact"}).text
        assert "Nothing was imported" in body

    def test_rejects_an_unknown_list(self, app_client, slug):
        response = app_client.post(f"/clients/{slug}/documents/import",
                                   data={"text": PLAIN, "list": "invented"})
        assert response.status_code == 400


class TestPicking:
    def save(self, app_client, slug, picks, text=EXPORT, **extra):
        data = {"text": text, "list": "redact", "action": "save", "pick": picks}
        data.update(extra)
        return app_client.post(f"/clients/{slug}/documents/import", data=data,
                               follow_redirects=False)

    def test_imports_only_the_ticked_rows(self, app_client, slug):
        self.save(app_client, slug, ["0", "2"])
        assert [r["instrument_number"] for r in stored(slug)] == ["2023111222",
                                                                  "2021998877"]

    def test_importing_none_is_refused_rather_than_treated_as_all(self, app_client,
                                                                  slug):
        body = self.save(app_client, slug, []).text
        assert "No documents were ticked" in body
        assert stored(slug) == []

    def test_an_out_of_range_tick_is_ignored_not_crashed_on(self, app_client, slug):
        self.save(app_client, slug, ["1", "9999", "-4", "not-a-number"])
        assert len(stored(slug)) == 1

    def test_party_names_are_not_written_to_the_client_file(self, app_client, slug):
        self.save(app_client, slug, ["0"])
        text = client_path(slug).read_text(encoding="utf-8")
        assert "OKONKWO" not in text and "grantor" not in text

    def test_reports_what_it_added(self, app_client, slug):
        response = self.save(app_client, slug, ["0", "1"])
        assert "added=2" in response.headers["location"]

    def test_a_second_import_keeps_the_first(self, app_client, slug):
        self.save(app_client, slug, ["0"])
        self.save(app_client, slug, ["1"])
        assert len(stored(slug)) == 2

    def test_re_importing_the_same_row_does_not_duplicate_it(self, app_client, slug):
        self.save(app_client, slug, ["0"])
        response = self.save(app_client, slug, ["0"])
        assert len(stored(slug)) == 1
        assert "added=0" in response.headers["location"]

    def test_marks_rows_already_on_file_in_the_next_preview(self, app_client, slug):
        self.save(app_client, slug, ["0"])
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"text": EXPORT, "list": "redact"}).text
        assert "already on file" in body

    def test_replace_clears_only_the_list_being_imported(self, app_client, slug):
        self.save(app_client, slug, ["0"])
        app_client.post(f"/clients/{slug}/documents/import", follow_redirects=False,
                        data={"text": PLAIN, "list": "release", "action": "save",
                              "pick": ["0"]})
        self.save(app_client, slug, ["1"], replace="1")

        counts = D.counts(load_client(client_path(slug)))
        assert counts == {"redact": 1, "release": 1}

    def test_limits_the_rows_to_chosen_people(self, app_client, slug):
        self.save(app_client, slug, ["0"], persons="primary")
        assert stored(slug)[0]["persons"] == ["primary"]


class TestUpload:
    def test_reads_an_uploaded_csv(self, app_client, slug):
        files = {"file": ("OfficialRecords_Results.csv", EXPORT, "text/csv")}
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"list": "redact"}, files=files).text
        assert len(pick_values(body)) == 3

    def test_refuses_a_spreadsheet_or_pdf_with_a_useful_message(self, app_client, slug):
        files = {"file": ("export.xlsx", b"PK\x03\x04\x00\x00binary\x00stuff",
                          "application/vnd.ms-excel")}
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"list": "redact"}, files=files).text
        assert "rather than a CSV" in body

    def test_refuses_something_far_too_big_to_be_a_record_list(self, app_client, slug):
        from webapp.routers.documents import MAX_IMPORT_BYTES

        files = {"file": ("huge.csv", "x" * (MAX_IMPORT_BYTES + 10), "text/csv")}
        body = app_client.post(f"/clients/{slug}/documents/import",
                               data={"list": "redact"}, files=files).text
        assert "too big" in body


class TestRowEditing:
    @pytest.fixture
    def with_rows(self, app_client, slug):
        app_client.post(f"/clients/{slug}/documents/import", follow_redirects=False,
                        data={"text": EXPORT, "list": "redact", "action": "save",
                              "pick": ["0", "1", "2"]})
        return slug

    def test_removes_a_ticked_row(self, app_client, with_rows):
        response = app_client.post(f"/clients/{with_rows}/documents",
                                   data={"rows[1][remove]": "1"},
                                   follow_redirects=False)
        assert "removed=1" in response.headers["location"]
        assert len(stored(with_rows)) == 2

    def test_moves_a_row_to_the_release_list(self, app_client, with_rows):
        app_client.post(f"/clients/{with_rows}/documents",
                        data={"rows[0][list]": "release"}, follow_redirects=False)
        assert D.counts(load_client(client_path(with_rows))) == {"redact": 2,
                                                                 "release": 1}

    def test_an_unknown_list_value_falls_back_rather_than_corrupting_the_row(
            self, app_client, with_rows):
        app_client.post(f"/clients/{with_rows}/documents",
                        data={"rows[0][list]": "invented"}, follow_redirects=False)
        assert all(r["list"] in D.LISTS for r in stored(with_rows))

    def test_posting_nothing_changes_nothing(self, app_client, with_rows):
        before = stored(with_rows)
        app_client.post(f"/clients/{with_rows}/documents", data={},
                        follow_redirects=False)
        assert stored(with_rows) == before


class TestWarnings:
    def test_says_an_attachment_will_be_generated(self, app_client, slug):
        rows = [{"instrument_number": f"A{i}", "title": "DEED"} for i in range(9)]
        save_client(slug, D.set_documents(load_client(client_path(slug)), rows))
        body = app_client.get(f"/clients/{slug}/documents").text
        assert "Attachment A" in body and "initial" in body
