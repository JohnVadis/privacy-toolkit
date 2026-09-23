"""Parsing a Clerk record list, and the store it lands in.

The header shapes below are not invented: the camelCase one is what Hillsborough's
"Export to Spreadsheet" actually writes, and the tab-separated one is what you get
by copying the results grid. Both were checked against a real export.
"""
from __future__ import annotations

import pytest

import documents as D

HILLSBOROUGH_EXPORT = (
    "Instrument,Grantor,Grantee,RecordDate,DocType,BookType,BookNum,PageNum,Legal,SalesPrice\n"
    '2019123456,"A CORP, INC.",SMITH JOHN,2019-05-14 11:28 AM,(D) WARRANTY DEED,O,26841,1234,LOT 3,100\n'
    "2026368918,CAPITAL ONE,SMITH LORENZO,2026-09-22 10:42 AM,(MOD) MODIFICATION,,,,,\n"
)

PLAIN_CSV = (
    "Instrument Number,Book,Page,Document Title\n"
    "2019123456,26841,1234,WARRANTY DEED\n"
)

PASTED_GRID = (
    "CFN\tBook/Page\tDoc Type\n"
    "2020111\t26000/12\tQUIT CLAIM DEED\n"
)


class TestParseHeaders:
    def test_reads_the_real_hillsborough_export(self):
        result = D.parse(HILLSBOROUGH_EXPORT)
        row = result["rows"][0]
        assert row["instrument_number"] == "2019123456"
        assert row["book"] == "26841"
        assert row["page"] == "1234"
        assert row["title"] == "(D) WARRANTY DEED"

    def test_booktype_does_not_steal_the_book_column(self):
        # BookType is "O" (the book's category); BookNum is the number the form wants.
        # An earlier prefix-matching pass let BookType claim Book and pushed BookNum
        # into "ignored", which silently printed the wrong value on a filing.
        result = D.parse(HILLSBOROUGH_EXPORT)
        assert result["rows"][0]["book"] == "26841"
        assert "BookType" in result["ignored"]

    def test_camelcase_headings_are_split(self):
        assert D._norm_header("DocType") == "doc type"
        assert D._norm_header("BookNum") == "book num"
        assert D._norm_header("Instrument #") == "instrument number"

    def test_reads_plain_headings(self):
        assert D.parse(PLAIN_CSV)["rows"][0]["title"] == "WARRANTY DEED"

    def test_reads_a_pasted_tab_separated_grid(self):
        row = D.parse(PASTED_GRID)["rows"][0]
        assert (row["book"], row["page"]) == ("26000", "12")   # split from "26000/12"

    def test_reads_a_qualified_heading(self):
        text = ("Instrument #,Book (Official Records),Page (Official Records),Doc Type\n"
                "2019123456,26841,1234,MORTGAGE\n")
        row = D.parse(text)["rows"][0]
        assert (row["book"], row["page"]) == ("26841", "1234")

    def test_reports_columns_it_ignored(self):
        assert set(D.parse(HILLSBOROUGH_EXPORT)["ignored"]) >= {"Legal", "SalesPrice"}

    def test_trims_the_clock_time_off_a_record_date(self):
        assert D.parse(HILLSBOROUGH_EXPORT)["rows"][0]["recorded"] == "2019-05-14"


class TestParseRefusals:
    def test_refuses_a_table_with_no_identifying_column(self):
        with pytest.raises(D.DocumentImportError) as exc:
            D.parse("Grantor,Grantee,Legal\nA,B,C\n")
        assert "Instrument Number" in str(exc.value)   # says what it looked for

    def test_refuses_empty_input(self):
        with pytest.raises(D.DocumentImportError):
            D.parse("   \n")

    def test_skips_rows_that_identify_no_record(self):
        text = PLAIN_CSV + ",,,JUNK WITH NO NUMBERS\n"
        result = D.parse(text)
        assert len(result["rows"]) == 1 and result["skipped"] == 1

    def test_keeps_a_row_with_only_an_instrument_number(self):
        # Modern Hillsborough records have no book/page at all.
        text = "Instrument,DocType\n2026368918,(MOD) MODIFICATION\n"
        assert len(D.parse(text)["rows"]) == 1

    def test_keeps_a_row_with_only_book_and_page(self):
        text = "Book,Page,Document Title\n26841,1234,DEED\n"
        assert len(D.parse(text)["rows"]) == 1

    def test_rejects_an_unknown_list_name(self):
        with pytest.raises(D.DocumentImportError):
            D.parse(PLAIN_CSV, default_list="something-else")


class TestPartyNames:
    def test_are_parsed_for_the_picker(self):
        row = D.parse(HILLSBOROUGH_EXPORT)["rows"][0]
        assert row["grantor"] == "A CORP, INC." and row["grantee"] == "SMITH JOHN"

    def test_but_are_never_stored_on_the_client(self):
        # A client file records the documents, not who else was on them.
        stored = D.normalize(D.parse(HILLSBOROUGH_EXPORT)["rows"][0])
        assert "grantor" not in stored and "grantee" not in stored

    def test_and_do_not_survive_set_documents(self):
        client = D.set_documents({"persons": []}, D.parse(HILLSBOROUGH_EXPORT)["rows"])
        assert not any("grantor" in row for row in client["documents"])


class TestMerge:
    def test_adds_new_rows(self):
        result = D.merge([], D.parse(PLAIN_CSV)["rows"])
        assert result["added"] == 1 and len(result["rows"]) == 1

    def test_a_second_import_does_not_discard_the_first(self):
        first = D.merge([], [{"instrument_number": "A1"}])["rows"]
        result = D.merge(first, [{"instrument_number": "B2"}])
        assert {r["instrument_number"] for r in result["rows"]} == {"A1", "B2"}

    def test_reimporting_the_same_export_changes_nothing(self):
        rows = D.parse(HILLSBOROUGH_EXPORT)["rows"]
        once = D.merge([], rows)["rows"]
        twice = D.merge(once, rows)
        assert twice["added"] == 0 and len(twice["rows"]) == len(once)

    def test_a_repeat_with_more_detail_updates_in_place(self):
        existing = [{"instrument_number": "A1", "title": "DEED"}]
        result = D.merge(existing, [{"instrument_number": "A1", "book": "26841"}])
        assert result["updated"] == 1 and len(result["rows"]) == 1
        assert result["rows"][0]["book"] == "26841"
        assert result["rows"][0]["title"] == "DEED"      # not clobbered

    def test_dedupes_rows_already_duplicated_in_the_stored_file(self):
        existing = [{"instrument_number": "A1"}, {"instrument_number": "A1"}]
        assert len(D.merge(existing, [])["rows"]) == 1

    def test_replace_drops_what_was_there(self):
        result = D.merge([{"instrument_number": "A1"}],
                         [{"instrument_number": "B2"}], replace=True)
        assert [r["instrument_number"] for r in result["rows"]] == ["B2"]

    def test_book_and_page_identify_a_row_with_no_instrument_number(self):
        a = {"book": "1", "page": "2"}
        assert D.merge([a], [dict(a)])["added"] == 0


class TestStore:
    def test_set_documents_places_the_key_after_persons(self):
        client = D.set_documents({"case": {}, "persons": [], "overrides": {}},
                                 [{"instrument_number": "A1"}])
        assert list(client) == ["case", "persons", "documents", "overrides"]

    def test_set_documents_with_nothing_omits_the_key(self):
        assert "documents" not in D.set_documents({"case": {}, "persons": []}, [])

    def test_a_malformed_documents_block_reads_as_empty(self):
        assert D.documents({"documents": "not a list"}) == []

    def test_for_form_splits_the_two_lists(self):
        client = {"documents": [{"instrument_number": "A1", "list": "redact"},
                                {"instrument_number": "B2", "list": "release"}]}
        assert len(D.for_form(client, "redact")) == 1
        assert len(D.for_form(client, "release")) == 1

    def test_for_form_honours_a_per_person_limit(self):
        client = {"documents": [
            {"instrument_number": "A1", "persons": ["primary"]},
            {"instrument_number": "B2"},                       # applies to everyone
        ]}
        assert len(D.for_form(client, "redact", "primary")) == 2
        assert len(D.for_form(client, "redact", "child")) == 1

    def test_counts_both_lists(self):
        client = {"documents": [{"instrument_number": "A1"},
                                {"instrument_number": "B2", "list": "release"}]}
        assert D.counts(client) == {"redact": 1, "release": 1}

    def test_an_unknown_list_value_falls_back_to_redact(self):
        assert D.normalize({"instrument_number": "A1", "list": "nonsense"})[
            "list"] == "redact"


def test_decode_handles_a_bom_and_cp1252():
    assert D.decode("Instrument\n".encode("utf-8-sig")).startswith("Instrument")
    assert "é" in D.decode("café".encode("cp1252"))


def test_parse_tolerates_a_leading_bom_that_survived_decoding():
    assert D.parse("﻿" + PLAIN_CSV)["rows"][0]["instrument_number"] == "2019123456"


def test_decode_refuses_something_that_is_not_text():
    with pytest.raises(D.DocumentImportError):
        D.decode(b"\x00\x01\x02\xff\xfe\x00\x00binary")
