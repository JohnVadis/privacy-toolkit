"""Adding a new form: are its boxes recognised, and is every one that isn't reported?"""
from __future__ import annotations

import pytest
import yaml

import form_import
import mapping_edit


@pytest.mark.parametrize("name, expected", [
    # the way PDF tools actually name boxes
    ("topmostSubform[0].Page1[0].txtFirstName[0]", "first_name"),
    ("Last_Name", "last_name"),
    ("LastName", "last_name"),
    ("MiddleInitial", "middle_name"),
    ("fname", "first_name"),
    ("DOB", "dob"),
    ("DateOfBirth", "dob"),
    ("Birthdate", "dob"),
    ("ZipCode", "home_zip"),
    ("Mailing Address City", "home_city"),
    ("Agent Name", "agent_name"),
    ("Employer Name", "employing_agency"),
    ("Postal Code", "home_zip"),
    ("City", "home_city"),
    ("State", "home_state"),
    ("County", "home_county"),
    ("Street Address", "home_line1"),
    ("Address Line 2", "home_line2"),
    ("Apt/Unit", "home_line2"),
    ("Residence Address", "home_full"),
    ("Mailing Address", "home_full"),
    ("Daytime Phone", "phone"),
    ("CellPhone", "phone"),
    ("E-mail Address", "email"),
    ("Agent Email", "agent_email"),
    ("Parcel ID", "home_parcel_id"),
    ("Folio Number", "home_parcel_id"),
    ("Applicant Name", "full_name"),
    ("Printed Name", "full_name"),
    ("Name", "full_name"),
    ("Case No", "case_number"),
    ("Employing Agency", "employing_agency"),
    ("Job Title", "job_title"),
])
def test_recognises_common_box_names(name, expected):
    assert form_import.match_variable(name) == expected


@pytest.mark.parametrize("name", [
    # someone else's details, or something a person signs — never the client's own
    "Spouse Name", "Child DOB", "Notary Public", "Signature", "Witness Name",
    "Prior Address", "Previous City", "Clerk Use Only", "Emergency Contact Phone",
    "Attorney Name",
    # an agent, an agency, another applicant: not the client's details either
    "Agent First Name", "AgentLastName", "Agent Phone", "Agent City", "Agent Zip",
    "Co-Applicant First Name", "Owner2FirstName", "Applicant 2 Last Name",
    "Joint Owner Last Name", "Mother First Name", "Father Last Name",
    "Second Applicant Name", "Name_2", "Employer Phone", "Agency Phone",
    "Supervisor Phone", "Agency City", "Agency Zip", "County of Employment",
    "Cancellation Date", "City where born", "City/State/ZIP",
    # the parent subform says whose box it is
    "topmostSubform[0].Spouse[0].FirstName[0]", "form1[0].Notary[0].Name[0]",
    "PriorAddress.Street",
    # nothing to go on
    "Text1", "Field 7", "Cite", "",
])
def test_leaves_boxes_it_should_not_guess(name):
    assert form_import.match_variable(name) == ""


def test_a_tooltip_about_someone_else_wins_over_the_box_name():
    assert form_import.match_variable("FirstName_2", "Spouse first name") == ""
    assert form_import.match_variable("Name", "Name of the notary") == ""
    assert form_import.match_variable("Text3", "Applicant's date of birth") == "dob"


def test_a_lone_street_box_gets_the_whole_address():
    fields = [{"id": "Street Address", "variable": "home_line1"}]
    form_import._widen_street(fields, [])
    assert fields[0]["variable"] == "home_full"
    fields = [{"id": "Street", "variable": "home_line1"}, {"id": "City", "variable": "home_city"}]
    form_import._widen_street(fields, [])
    assert fields[0]["variable"] == "home_line1"


def test_readable_name_unpacks_tool_generated_names():
    assert form_import.readable_name("topmostSubform[0].Page1[0].txtFirstName[0]") == "First Name"
    assert form_import.readable_name("ZipCode2") == "Zip Code 2"


@pytest.fixture
def fillable_pdf(tmp_path):
    """A small fillable PDF: recognised names, a tooltip-only box, a mystery box."""
    from reportlab.pdfgen import canvas

    path = tmp_path / "fillable.pdf"
    c = canvas.Canvas(str(path))
    form = c.acroForm
    form.textfield(name="FirstName", x=72, y=700, width=200, height=18)
    form.textfield(name="ZipCode", x=72, y=670, width=200, height=18)
    # The name says nothing; the tooltip is what a person reads.
    form.textfield(name="Text3", tooltip="Applicant's date of birth", x=72, y=640,
                   width=200, height=18)
    form.textfield(name="Text4", x=72, y=610, width=200, height=18)
    form.checkbox(name="Agree", x=72, y=580, size=12)
    c.showPage()
    c.save()
    return path


def test_every_box_is_either_matched_or_reported(fillable_pdf):
    info = form_import.analyze(fillable_pdf)
    by_id = {f["id"]: f["variable"] for f in info["fields"]}
    assert by_id["FirstName"] == "first_name"
    assert by_id["ZipCode"] == "home_zip"
    assert by_id["Text3"] == "dob"            # from the tooltip
    assert info["unmatched_fields"] == ["Text4"]
    assert info["checkbox_fields"] == ["Agree"]


def test_the_proposal_fills_what_was_recognised(fillable_pdf):
    mapping, notes = form_import.suggest(fillable_pdf, "new_form", county="Pinellas")
    assert mapping["mode"] == "fillable" and mapping["county"] == "Pinellas"
    assert mapping["fields"]["Text3"] == {"from": "dob"}
    assert mapping["fields"]["Text4"] == {"literal": ""}
    assert any("weren't recognised" in n for n in notes)


def test_coverage_counts_boxes_left_blank(fillable_pdf):
    mapping, _ = form_import.suggest(fillable_pdf, "new_form")
    cov = mapping_edit.field_coverage(mapping)
    assert cov["total"] == 5
    assert cov["blank"] == ["Text4"]          # the checkbox has a rule, so it's set
    assert cov["missing"] == []


def test_a_box_name_the_pdf_does_not_have_is_caught(fillable_pdf):
    mapping, _ = form_import.suggest(fillable_pdf, "new_form")
    mapping["fields"]["FristName"] = {"from": "first_name"}
    _, problems = mapping_edit.check_mapping(yaml.safe_dump(mapping), expect_key="new_form")
    assert any("FristName" in p for p in problems)


def test_an_image_only_scan_can_still_be_created(tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(path))
    c.rect(72, 600, 300, 100)                 # lines, no text layer
    c.save()
    mapping, notes = form_import.suggest(path, "scan_form")
    assert mapping["entries"] == []
    _, problems = mapping_edit.check_mapping(yaml.safe_dump(mapping), expect_key="scan_form")
    assert problems == []
