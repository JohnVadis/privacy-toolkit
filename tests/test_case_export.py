"""Packaging a case into one file, checking it, and putting it back."""
from __future__ import annotations

import json
import zipfile

import pytest
import yaml

import case_export as CE
import fill_forms as FF
import paths
from client_context import (ClientDataError, client_path, load_client,
                            output_dir, save_client)


@pytest.fixture
def mapping(flat_form, tmp_path):
    data = {"mode": "overlay", "form_key": "exported", "pdf": str(flat_form),
            "entries": [{"page": 1, "x": 160, "y": 700, "from": "full_name"}]}
    path = tmp_path / "exported.map.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def case(saved_client, mapping):
    """A client with a generated form, ready to export."""
    FF.fill_one(mapping, saved_client, None, report=lambda *_: None)
    return "Example Client", saved_client


def names_in(data: bytes) -> set[str]:
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return set(archive.namelist())


class TestBuild:
    def test_includes_the_client_file(self, case):
        slug, client = case
        data, _ = CE.build(client, slug)
        assert f"clients/{slug}.yaml" in names_in(data)

    def test_includes_every_generated_file(self, case):
        slug, client = case
        data, _ = CE.build(client, slug)
        assert "output/Example Client/exported__primary.pdf" in names_in(data)

    def test_includes_the_generation_log(self, case):
        # The record of what was produced belongs with the things it describes.
        slug, client = case
        data, _ = CE.build(client, slug)
        assert "output/Example Client/.generation-log.jsonl" in names_in(data)

    def test_includes_a_manifest_and_a_readme(self, case):
        slug, client = case
        data, _ = CE.build(client, slug)
        assert {CE.MANIFEST_NAME, CE.README_NAME} <= names_in(data)

    def test_the_readme_warns_it_is_not_encrypted(self, case):
        slug, client = case
        data, _ = CE.build(client, slug)
        import io

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            readme = archive.read(CE.README_NAME).decode("utf-8")
        assert "not encrypted" in readme and "PERSONAL INFORMATION" in readme

    def test_the_manifest_hashes_every_file(self, case):
        slug, client = case
        data, book = CE.build(client, slug)
        assert book["file_count"] == len(book["files"])
        assert all(len(e["sha256"]) == 64 for e in book["files"])

    def test_the_manifest_identifies_the_case(self, case):
        slug, client = case
        _, book = CE.build(client, slug)
        assert book["slug"] == slug
        assert book["display_name"] == "Example, Alex"
        assert book["exported_at"]

    def test_a_case_with_nothing_generated_still_exports(self, saved_client):
        data, book = CE.build(saved_client, "Example Client")
        assert book["file_count"] == 1          # just the client file
        assert "clients/Example Client.yaml" in names_in(data)

    def test_nothing_is_written_to_disk_while_building(self, case):
        slug, client = case
        before = set(paths.ROOT.rglob("*.zip"))
        CE.build(client, slug)
        assert set(paths.ROOT.rglob("*.zip")) == before


class TestExportToDisk:
    def test_writes_into_exports_by_default(self, case):
        slug, client = case
        path, _ = CE.export(slug)
        assert path.parent == CE.EXPORTS_DIR and path.is_file()

    def test_writes_where_it_is_told(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)
        assert path.parent == tmp_path

    def test_the_name_says_whose_case_and_when(self, case):
        slug, client = case
        path, _ = CE.export(slug)
        assert slug in path.name and path.suffix == ".zip"

    def test_an_unknown_client_is_an_error(self):
        with pytest.raises(ClientDataError):
            CE.export("Nobody At All")

    def test_exports_are_gitignored(self):
        from conftest import REPO

        ignore = REPO / ".gitignore"
        if not ignore.is_file():
            pytest.skip("running outside a checkout")
        assert "exports/" in ignore.read_text(encoding="utf-8")


class TestVerify:
    def test_a_fresh_export_checks_out(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)
        assert CE.verify(path)["ok"]

    def test_it_counts_what_it_checked(self, case, tmp_path):
        slug, client = case
        path, book = CE.export(slug, tmp_path)
        assert CE.verify(path)["checked"] == book["file_count"]

    def test_a_tampered_file_is_caught(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)

        # Rebuild the zip with one member's contents changed.
        import io

        with zipfile.ZipFile(path) as original:
            members = {n: original.read(n) for n in original.namelist()}
        members[f"clients/{slug}.yaml"] = b"case: {display_name: Someone Else}\n"
        with zipfile.ZipFile(path, "w") as rebuilt:
            for name, data in members.items():
                rebuilt.writestr(name, data)

        result = CE.verify(path)
        assert not result["ok"] and result["corrupt"]

    def test_a_missing_file_is_caught(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)

        with zipfile.ZipFile(path) as original:
            members = {n: original.read(n) for n in original.namelist()}
        dropped = f"clients/{slug}.yaml"
        members.pop(dropped)
        with zipfile.ZipFile(path, "w") as rebuilt:
            for name, data in members.items():
                rebuilt.writestr(name, data)

        assert dropped in CE.verify(path)["missing"]

    def test_a_zip_that_is_not_an_export_says_so(self, tmp_path):
        path = tmp_path / "random.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("notes.txt", "nothing to do with the toolkit")
        with pytest.raises(ClientDataError):
            CE.verify(path)


class TestRestore:
    def test_the_archive_mirrors_the_layout_it_came_from(self, case):
        # So a restore puts files back where they were, rather than in output/ root.
        slug, client = case
        data, _ = CE.build(client, slug)
        assert all(n.startswith(f"output/{output_dir(client).name}/")
                   for n in names_in(data) if n.startswith("output/"))

    def test_puts_the_client_and_its_output_back(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)

        client_path(slug).unlink()
        import shutil

        shutil.rmtree(output_dir(client))

        result = CE.restore(path)
        assert client_path(slug).is_file()
        assert (output_dir(load_client(client_path(slug)))
                / "exported__primary.pdf").is_file()
        assert result["slug"] == slug

    def test_the_restored_client_file_is_identical(self, case, tmp_path):
        slug, client = case
        before = client_path(slug).read_bytes()
        path, _ = CE.export(slug, tmp_path)
        client_path(slug).unlink()

        CE.restore(path)
        assert client_path(slug).read_bytes() == before

    def test_it_refuses_to_overwrite_a_client_that_exists(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)
        with pytest.raises(ClientDataError) as exc:
            CE.restore(path)
        assert "already exists" in str(exc.value)

    def test_replace_allows_it(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)
        client_path(slug).write_text("case: {display_name: Changed}\n", encoding="utf-8")

        CE.restore(path, replace=True)
        assert "Example, Alex" in client_path(slug).read_text(encoding="utf-8")

    def test_a_damaged_archive_is_never_unpacked(self, case, tmp_path):
        slug, client = case
        path, _ = CE.export(slug, tmp_path)
        client_path(slug).unlink()

        with zipfile.ZipFile(path) as original:
            members = {n: original.read(n) for n in original.namelist()}
        members[f"clients/{slug}.yaml"] = b"tampered"
        with zipfile.ZipFile(path, "w") as rebuilt:
            for name, data in members.items():
                rebuilt.writestr(name, data)

        with pytest.raises(ClientDataError):
            CE.restore(path)
        assert not client_path(slug).exists()     # nothing was written


class TestZipSlip:
    """A zip can name its members anything, including a path outside the target."""

    @pytest.mark.parametrize("name", [
        "../escaped.yaml",
        "clients/../../escaped.yaml",
        "/absolute.yaml",
        "output/../../../escaped.pdf",
    ])
    def test_a_member_pointing_outside_the_toolkit_is_refused(self, name, tmp_path):
        assert CE._safe_target(name, tmp_path) is None

    @pytest.mark.parametrize("name", ["clients/x.yaml", "output/form.pdf",
                                      "output/nested/deep/file.pdf"])
    def test_an_ordinary_member_is_allowed(self, name, tmp_path):
        target = CE._safe_target(name, tmp_path)
        assert target is not None and tmp_path in target.parents

    def test_restore_skips_an_unsafe_member_rather_than_writing_it(self, tmp_path):
        root = tmp_path / "toolkit"
        root.mkdir()
        archive_path = tmp_path / "evil.zip"

        payload = b"you should not see this"
        entries = [{"name": "../escaped.yaml", "bytes": len(payload),
                    "sha256": CE._sha256_bytes(payload)}]
        book = {"format": 1, "slug": "Victim", "display_name": "V",
                "file_count": 1, "total_bytes": len(payload), "files": entries}
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escaped.yaml", payload)
            archive.writestr(CE.MANIFEST_NAME, json.dumps(book))

        result = CE.restore(archive_path, root=root)
        assert result["skipped"] == ["../escaped.yaml"]
        assert not (tmp_path / "escaped.yaml").exists()
        assert result["written"] == []


class TestRoute:
    def test_downloads_the_case_as_a_zip(self, app_client, case):
        slug, client = case
        response = app_client.get(f"/clients/{slug}/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"

    def test_it_is_offered_as_a_download_with_a_readable_name(self, app_client, case):
        slug, client = case
        disposition = app_client.get(
            f"/clients/{slug}/export").headers["content-disposition"]
        assert disposition.startswith("attachment;") and ".zip" in disposition

    def test_the_download_is_a_real_archive(self, app_client, case):
        import io

        slug, client = case
        data = app_client.get(f"/clients/{slug}/export").content
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert CE.MANIFEST_NAME in archive.namelist()

    def test_it_is_never_cached(self, app_client, case):
        slug, client = case
        headers = app_client.get(f"/clients/{slug}/export").headers
        assert "no-store" in headers["cache-control"]

    def test_no_copy_is_left_on_the_machine(self, app_client, case):
        # Built in memory; writing it under output/ would leave a second plaintext
        # copy of the whole case lying about.
        slug, client = case
        before = set(output_dir(client).iterdir())
        app_client.get(f"/clients/{slug}/export")
        assert set(output_dir(client).iterdir()) == before

    def test_an_unknown_client_is_a_404(self, app_client):
        assert app_client.get("/clients/Nobody/export").status_code == 404
