"""Pre-flight validation. It checks PRESENCE, never eligibility."""
from __future__ import annotations

import copy

import pytest

import documents as D
from validate_client import errors, issues_by_field, validate_client, warnings


def fields(issues):
    return {i.field for i in issues}


@pytest.fixture
def valid(example_client):
    return example_client


class TestBlockingErrors:
    def test_a_complete_client_has_none(self, valid):
        assert errors(validate_client(valid)) == []

    def test_display_name_is_required(self, valid):
        valid["case"]["display_name"] = ""
        assert "case.display_name" in fields(errors(validate_client(valid)))

    def test_at_least_one_person_is_required(self, valid):
        valid["persons"] = []
        assert "persons" in fields(errors(validate_client(valid)))

    def test_a_person_needs_a_role(self, valid):
        valid["persons"][0]["role"] = ""
        assert "persons.0.role" in fields(errors(validate_client(valid)))

    def test_an_unknown_role_is_rejected(self, valid):
        valid["persons"][0]["role"] = "cousin"
        assert "persons.0.role" in fields(errors(validate_client(valid)))

    def test_two_people_cannot_share_a_role(self, valid):
        valid["persons"].append(copy.deepcopy(valid["persons"][0]))
        assert "persons.1.role" in fields(errors(validate_client(valid)))

    def test_a_person_needs_a_name(self, valid):
        valid["persons"][0]["first_name"] = ""
        valid["persons"][0]["last_name"] = ""
        assert "persons.0.first_name" in fields(errors(validate_client(valid)))

    def test_date_of_birth_is_required(self, valid):
        valid["persons"][0]["dob"] = ""
        assert "persons.0.dob" in fields(errors(validate_client(valid)))

    def test_the_residence_county_is_required(self, valid):
        valid["persons"][0]["addresses"][0]["county"] = ""
        assert any(f.endswith(".county") for f in fields(errors(validate_client(valid))))

    def test_an_unknown_statutory_category_is_rejected(self, valid):
        valid["case"]["exemption"]["category"] = "firefighter"
        assert "case.exemption.category" in fields(errors(validate_client(valid)))

    def test_it_never_decides_whether_the_client_qualifies(self, valid):
        # A complete file with a valid category passes regardless of job title or
        # agency — eligibility is a human judgement, by design.
        valid["case"]["exemption"]["job_title"] = ""
        valid["case"]["exemption"]["employing_agency"] = ""
        assert errors(validate_client(valid)) == []


class TestWarnings:
    def test_an_empty_residence_field_warns_without_blocking(self, valid):
        valid["persons"][0]["addresses"][0]["zip"] = ""
        found = validate_client(valid)
        assert errors(found) == []
        assert any(f.endswith(".zip") for f in fields(warnings(found)))

    def test_no_marked_residence_warns(self, valid):
        valid["persons"][0]["addresses"][0]["residence"] = False
        assert warnings(validate_client(valid))

    def test_issues_group_by_field_for_inline_display(self, valid):
        valid["persons"][0]["dob"] = ""
        assert "persons.0.dob" in issues_by_field(validate_client(valid))


class TestDocumentWarnings:
    def _docs(self, issues):
        return [i for i in issues if i.label == "Documents"]

    def test_says_when_nothing_has_been_imported(self, valid):
        found = self._docs(validate_client(valid, ["hillsborough_clerk_redaction"]))
        assert any("no documents imported" in i.message for i in found)

    def test_warns_when_a_row_names_nobody_on_the_client(self, valid):
        client = D.set_documents(valid, [{"instrument_number": "A1",
                                          "persons": ["cousin"]}])
        found = self._docs(validate_client(client, ["hillsborough_clerk_redaction"]))
        assert any("nobody on this client" in i.message for i in found)

    def test_warns_about_a_document_with_no_title(self, valid):
        client = D.set_documents(valid, [{"instrument_number": "A1"}])
        found = self._docs(validate_client(client, ["hillsborough_clerk_redaction"]))
        assert any("no document title" in i.message for i in found)

    def test_warns_that_an_attachment_will_be_needed(self, valid):
        rows = [{"instrument_number": f"A{i}", "title": "DEED"} for i in range(9)]
        client = D.set_documents(valid, rows)
        found = self._docs(validate_client(client, ["hillsborough_clerk_redaction"]))
        assert any("Attachment" in i.message for i in found)

    def test_warns_when_a_title_will_print_cut_short(self, valid):
        client = D.set_documents(valid, [
            {"instrument_number": "A1", "title": "NOTICE OF COMMENCEMENT AND "
                                                 "AFFIDAVIT OF OWNERSHIP AND AUTHORITY "
                                                 "AND SEVERAL OTHER THINGS BESIDES"}])
        found = self._docs(validate_client(client, ["hillsborough_clerk_redaction"]))
        assert any("cut short" in i.message for i in found)

    def test_documents_never_block_generation(self, valid):
        client = D.set_documents(valid, [{"instrument_number": "A1"}])
        assert errors(validate_client(client)) == []
