"""The audit trail: what was produced, from what, and whether it still matches."""
from __future__ import annotations

import json

import pytest
import yaml

import fill_forms as FF
import generation_log as GL
from client_context import output_dir


@pytest.fixture
def mapping(flat_form, tmp_path):
    data = {"mode": "overlay", "form_key": "logged", "title": "Logged form",
            "pdf": str(flat_form),
            "entries": [{"page": 1, "x": 160, "y": 700, "from": "full_name"}]}
    path = tmp_path / "logged.map.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def generated(mapping, saved_client):
    out = FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
    return out, output_dir(saved_client)


class TestRecording:
    def test_every_fill_writes_a_line(self, generated):
        out, folder = generated
        assert len(GL.read(folder)) == 1

    def test_the_log_sits_beside_the_output(self, generated):
        out, folder = generated
        assert (folder / GL.LOG_NAME).is_file()

    def test_it_is_hidden_from_the_file_list(self, generated, saved_client):
        # It must never be offered as something to send to a county.
        out, folder = generated
        assert GL.LOG_NAME not in [r["filename"] for r in FF.generated_files(saved_client)]

    def test_it_names_the_form_and_the_person(self, generated):
        out, folder = generated
        record = GL.read(folder)[0]
        assert record["form_key"] == "logged" and record["person"] == "primary"

    def test_it_records_the_produced_file_and_its_checksum(self, generated):
        out, folder = generated
        record = GL.read(folder)[0]
        assert record["output"] == out.name
        assert len(record["output_sha256"]) == 64

    def test_it_records_what_produced_it(self, generated):
        # Which mapping, and which blank form underneath — enough to reproduce.
        out, folder = generated
        record = GL.read(folder)[0]
        assert record["mapping"] == "logged.map.yaml"
        assert len(record["mapping_sha256"]) == 64
        assert len(record["blank_pdf_sha256"]) == 64

    def test_it_records_the_libraries_that_did_the_drawing(self, generated):
        # reportlab decides where a glyph sits; a filed form should say which one.
        out, folder = generated
        versions = GL.read(folder)[0]["versions"]
        assert "reportlab" in versions and "pypdf" in versions
        assert versions["python"]

    def test_it_appends_rather_than_replaces(self, mapping, saved_client):
        FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
        FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
        assert len(GL.read(output_dir(saved_client))) == 2

    def test_each_line_is_valid_json(self, generated):
        out, folder = generated
        text = (folder / GL.LOG_NAME).read_text(encoding="utf-8")
        for line in text.splitlines():
            json.loads(line)

    def test_the_document_count_is_recorded_for_a_form_with_a_table(
            self, flat_form, tmp_path, saved_client):
        import documents as D

        data = {"mode": "overlay", "form_key": "tabled", "pdf": str(flat_form),
                "entries": [{"page": 1, "x": 160, "y": 700, "from": "full_name"}],
                "tables": [{"key": "d", "list": "redact", "page": 1, "rows": 3,
                            "first_y": 600, "row_pitch": 18,
                            "columns": [{"from": "instrument_number", "x": 74,
                                         "width": 138}]}]}
        path = tmp_path / "tabled.map.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        client = D.set_documents(saved_client, [{"instrument_number": "A1"},
                                                {"instrument_number": "A2"}])
        FF.fill_one(path, client, None, report=lambda *_: None)
        assert GL.read(output_dir(client))[-1]["documents"] == 2


class TestItNeverBreaksAFill:
    def test_a_log_failure_does_not_lose_the_form(self, mapping, saved_client,
                                                  monkeypatch):
        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(GL, "entry", boom)
        out = FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
        assert out.is_file()          # the filing survived; only its line was lost

    def test_record_returns_none_rather_than_raising(self, tmp_path, monkeypatch):
        monkeypatch.setattr(GL, "entry", lambda **k: {"unserializable": object()})
        assert GL.record(tmp_path / "x.pdf", form_key="f", person="p") is None


class TestVerify:
    def test_an_untouched_file_reads_as_unchanged(self, generated):
        out, folder = generated
        assert GL.verify(folder, out.name)["status"] == "unchanged"

    def test_an_edited_file_is_reported_as_modified(self, generated):
        # Expected once a PDF has been signed or annotated — and worth knowing.
        out, folder = generated
        out.write_bytes(out.read_bytes() + b"\n% annotated\n")
        assert GL.verify(folder, out.name)["status"] == "modified"

    def test_a_file_with_no_record_says_so(self, generated):
        out, folder = generated
        (folder / "hand_made.pdf").write_bytes(b"%PDF-1.4\n")
        assert GL.verify(folder, "hand_made.pdf")["status"] == "unlogged"

    def test_a_missing_file_says_so(self, generated):
        out, folder = generated
        out.unlink()
        assert GL.verify(folder, out.name)["status"] == "missing"

    def test_it_counts_how_many_times_a_form_was_generated(self, mapping,
                                                            saved_client):
        for _ in range(3):
            out = FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
        assert GL.verify(output_dir(saved_client), out.name)["generations"] == 3

    def test_it_compares_against_the_most_recent_generation(self, mapping,
                                                             saved_client):
        FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
        saved_client["case"]["display_name"] = "Example, Alex"    # same folder
        out = FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
        assert GL.verify(output_dir(saved_client), out.name)["status"] == "unchanged"


class TestReading:
    def test_an_absent_log_reads_as_empty(self, tmp_path):
        assert GL.read(tmp_path) == []

    def test_a_corrupt_line_is_skipped_not_fatal(self, generated):
        out, folder = generated
        with open(folder / GL.LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write("{not json at all\n")
        assert len(GL.read(folder)) == 1

    def test_history_is_newest_first(self, mapping, saved_client):
        for _ in range(2):
            out = FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
        rows = GL.history(output_dir(saved_client), out.name)
        assert rows[0]["at"] >= rows[1]["at"]
