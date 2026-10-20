"""Adding a county: one spelling, and clients get their own county's forms."""
from __future__ import annotations

import copy
import shutil

import pytest
import yaml

import counties
import fill_forms
import paths
from client_context import save_client
from validate_client import validate_client


@pytest.mark.parametrize("typed, county", [
    ("Hillsborough", "Hillsborough"),
    ("hillsborough county", "Hillsborough"),
    ("Hillsborough Co.", "Hillsborough"),
    ("HILLSBOROUGH COUNTY, FL", "Hillsborough"),
    ("pinellas", "Pinellas"),
    ("Saint Johns", "St. Johns"),
    ("st johns county", "St. Johns"),
    ("Miami Dade", "Miami-Dade"),
    ("Dade", "Miami-Dade"),
    ("desoto", "DeSoto"),
    ("indian river", "Indian River"),
    ("statewide", "statewide"),          # a CLIENT is never "Statewide"...
    ("Florida", "Florida"),
    ("  ", ""),
    ("Cook", "Cook"),                    # out of state: kept as typed
])
def test_normalise(typed, county):
    assert counties.normalise(typed) == county


def test_a_forms_county_can_be_statewide():
    # ...but a FORM can: that's how everyone gets it.
    assert counties.normalise("florida", form=True) == "Statewide"
    assert counties.normalise("Pinellas County", form=True) == "Pinellas"


def test_all_67_counties():
    assert len(counties.FLORIDA_COUNTIES) == 67 == len(set(counties.FLORIDA_COUNTIES))


def test_a_typo_gets_a_suggestion():
    assert counties.suggestion("Pinelas") == "Pinellas"
    assert counties.suggestion("Hillsborough") == ""
    assert counties.suggestion("Cook") == ""


def with_county(client, county):
    c = copy.deepcopy(client)
    for a in c["persons"][0]["addresses"]:
        a["county"] = county
    return c


def keys(rows):
    return sorted(m["form_key"] for m in rows)


def test_a_hillsborough_client_gets_hillsborough_and_statewide_forms(example_client):
    got = keys(fill_forms.forms_for_client(with_county(example_client, "Hillsborough")))
    assert "hillsborough_clerk_redaction" in got
    assert "fl_dos_public_records_exemption" in got
    assert "demo_redaction" not in got


def test_a_pinellas_client_gets_only_statewide_until_pinellas_forms_exist(example_client):
    got = keys(fill_forms.forms_for_client(with_county(example_client, "pinellas county")))
    assert got == ["fl_dos_public_records_exemption"]


@pytest.fixture
def pinellas_form():
    """A new county's form added the way the app adds one: a mapping and a PDF."""
    path = paths.MAPPINGS_DIR / "pinellas_clerk_redaction.map.yaml"
    path.write_text(yaml.safe_dump({
        "form_key": "pinellas_clerk_redaction", "title": "Pinellas Clerk redaction",
        "county": "Pinellas", "mode": "overlay",
        "pdf": "forms/_blank_pdfs/Hillsborough_Redaction.pdf", "entries": []}),
        encoding="utf-8")
    yield "pinellas_clerk_redaction"
    path.unlink(missing_ok=True)


def test_a_new_county_form_reaches_its_clients_with_no_code(example_client, pinellas_form):
    got = keys(fill_forms.forms_for_client(with_county(example_client, "Pinellas")))
    assert got == ["fl_dos_public_records_exemption", pinellas_form]
    hills = keys(fill_forms.forms_for_client(with_county(example_client, "Hillsborough")))
    assert pinellas_form not in hills


def test_a_new_county_form_with_a_tracked_pdf_is_packaged_with_no_code(pinellas_form):
    # The mapping is found, not listed; its PDF is one .gitignore lets through.
    assert pinellas_form in fill_forms.shipped_form_keys()
    assert "demo_redaction" not in fill_forms.shipped_form_keys()


def test_a_form_whose_pdf_git_ignores_is_never_packaged():
    # A PDF dropped in _blank_pdfs/ that isn't on .gitignore's list of real forms
    # could be a scratch upload or a client's own paperwork: it stays behind.
    pdf = paths.BLANK_PDF_DIR / "pinellas_scratch_upload.pdf"
    shutil.copy2(paths.BLANK_PDF_DIR / "Hillsborough_Redaction.pdf", pdf)
    mapping = paths.MAPPINGS_DIR / "pinellas_scratch.map.yaml"
    mapping.write_text(yaml.safe_dump({
        "form_key": "pinellas_scratch", "county": "Pinellas", "mode": "overlay",
        "pdf": "forms/_blank_pdfs/pinellas_scratch_upload.pdf", "entries": []}),
        encoding="utf-8")
    try:
        assert "pinellas_scratch" not in fill_forms.shipped_form_keys()
    finally:
        mapping.unlink()
        pdf.unlink()


def test_an_unknown_county_is_a_warning_with_a_suggestion(example_client):
    found = validate_client(with_county(example_client, "Pinelas"))
    county = [i for i in found if i.field.endswith(".county")]
    assert county and county[0].severity == "warning"
    assert "Did you mean Pinellas?" in county[0].message


def test_a_pinellas_client_is_not_warned_about_hillsborough_forms(example_client):
    found = validate_client(with_county(example_client, "Pinellas"))
    assert not any("hillsborough" in (i.form_key or "") for i in found)


class TestTheApp:
    def test_saving_a_client_spells_the_county_one_way(self, app_client, example_client):
        save_client("County Client", with_county(example_client, "Hillsborough"))
        page = app_client.get("/clients/County Client/edit")
        assert 'list="florida-counties"' in page.text
        assert '<option value="Pinellas">' in page.text
        from webapp.model import _address_from_form
        assert _address_from_form({"county": "pinellas county"})["county"] == "Pinellas"

    def test_the_generate_page_ticks_the_clients_own_forms(self, app_client, example_client,
                                                           pinellas_form):
        save_client("Pinellas Client", with_county(example_client, "Pinellas"))
        page = app_client.get("/clients/Pinellas Client/generate").text
        mine, others = page.split("Other counties", 1)
        assert f'value="{pinellas_form}" checked' in mine
        assert 'value="fl_dos_public_records_exemption" checked' in mine
        assert 'value="hillsborough_clerk_redaction"' in others
        assert 'value="hillsborough_clerk_redaction" checked' not in page

    def test_making_forms_with_none_ticked_makes_only_the_clients_own(
            self, app_client, example_client):
        save_client("Pinellas Client", with_county(example_client, "Pinellas"))
        app_client.post("/clients/Pinellas Client/generate", data={})
        made = app_client.get("/clients/Pinellas Client/files").text
        assert "Florida" in made or "fl_dos" in made.lower() or "Public Records" in made
        assert "Clerk of Court" not in made
