"""The handoff copy: that it is complete, and that it carries no client data.

This builds from an ALLOWLIST rather than a list of exclusions, which is the right
way round — but an allowlist rots silently. Add a module, forget to name it, and the
copy still builds and still zips, then fails on the recipient's first double-click
with an ImportError they cannot fix. So the closure check below matters as much as
the privacy one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import make_test_copy as M
from conftest import REPO


def local_modules() -> set[str]:
    return {p.stem for p in REPO.glob("*.py")}


def shipped_modules() -> set[str]:
    return {Path(f).stem for f in M.FILES if f.endswith(".py")}


def imports_of(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


class TestTheAllowlistIsComplete:
    def test_every_module_it_ships_can_import_what_it_needs(self):
        sources = [REPO / f for f in M.FILES if f.endswith(".py")]
        sources += list((REPO / "webapp").rglob("*.py"))

        needed = set()
        for path in sources:
            if path.is_file():
                needed |= imports_of(path)

        missing = sorted((needed & local_modules()) - shipped_modules() - {"webapp"})
        assert not missing, (
            f"the copy would fail on first run — not in FILES: {missing}")

    def test_it_ships_the_launcher_for_each_platform(self):
        assert "Start Privacy Toolkit.bat" in M.FILES
        assert "Start Privacy Toolkit.command" in M.FILES

    def test_it_ships_the_client_template(self):
        assert "clients/_TEMPLATE.yaml" in M.FILES

    def test_it_ships_the_whole_web_app(self):
        assert "webapp" in M.FOLDERS

    def test_every_named_file_actually_exists(self):
        missing = [f for f in M.FILES if not (REPO / f).exists()]
        assert not missing, f"FILES names things that aren't here: {missing}"

    def test_every_named_form_has_a_mapping(self):
        missing = [k for k in M.FORM_KEYS
                   if not (REPO / "forms" / "mappings" / f"{k}.map.yaml").is_file()]
        assert not missing, f"FORM_KEYS names mappings that aren't here: {missing}"


class TestItLeavesThePrivateThingsBehind:
    @pytest.mark.parametrize("name", [
        "build_exe.py", "make_test_copy.py", "CLAUDE.md", "requirements-dev.txt",
        "pytest.ini",
    ])
    def test_maintainer_only_files_do_not_travel(self, name):
        assert name not in M.FILES

    def test_no_real_client_file_is_named(self):
        named = [f for f in M.FILES if f.startswith("clients/")]
        assert named == ["clients/_TEMPLATE.yaml"]

    @pytest.mark.parametrize("folder", ["clients", "output", "trash", "feedback",
                                        "exports", "docs", "tests"])
    def test_no_folder_of_client_data_or_dev_tooling_travels(self, folder):
        assert folder not in M.FOLDERS

    def test_the_demo_and_scratch_mappings_stay_behind(self):
        # They aren't real county forms; shipping them invites a filing on one.
        assert "demo_redaction" not in M.FORM_KEYS
        assert "test" not in M.FORM_KEYS

    def test_backups_and_caches_are_skipped_when_copying_folders(self):
        assert {"__pycache__", ".venv", "_backups"} <= M.SKIP_DIRS


class TestTheArchive:
    """Windows has no execute bit, so the zip has to carry it deliberately."""

    def test_the_mac_launcher_comes_out_executable(self, tmp_path):
        import zipfile

        dest = tmp_path / "Privacy Toolkit (test copy)"
        dest.mkdir()
        (dest / "Start Privacy Toolkit.command").write_text(
            "#!/bin/bash\n", encoding="utf-8")
        (dest / "Start Privacy Toolkit.bat").write_text(
            "@echo off\n", encoding="utf-8")
        (dest / "paths.py").write_text("x = 1\n", encoding="utf-8")

        archive = M.zip_copy(dest)
        modes = {}
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                modes[info.filename.split("/")[-1]] = (info.external_attr >> 16) & 0o777

        # The one file a Mac user is told to double-click.
        assert modes["Start Privacy Toolkit.command"] & 0o111
        assert not modes["paths.py"] & 0o111

    def test_the_archive_holds_the_folder_not_its_contents_loose(self, tmp_path):
        import zipfile

        dest = tmp_path / "Privacy Toolkit (test copy)"
        dest.mkdir()
        (dest / "paths.py").write_text("x = 1\n", encoding="utf-8")

        with zipfile.ZipFile(M.zip_copy(dest)) as zf:
            assert all(n.startswith("Privacy Toolkit (test copy)/")
                       for n in zf.namelist())

    def test_rebuilding_replaces_the_previous_archive(self, tmp_path):
        dest = tmp_path / "copy"
        dest.mkdir()
        (dest / "paths.py").write_text("x = 1\n", encoding="utf-8")
        first = M.zip_copy(dest)
        (dest / "extra.py").write_text("y = 2\n", encoding="utf-8")
        second = M.zip_copy(dest)

        import zipfile

        assert first == second
        with zipfile.ZipFile(second) as zf:
            assert len(zf.namelist()) == 2


class TestThePrivacyScan:
    def test_it_looks_for_values_from_the_real_client_files(self):
        # The scan reads clients/*.yaml on the machine building the copy; in the
        # sandbox there are none, so it must cope with that rather than crash.
        assert isinstance(M.private_markers(), list)

    def test_it_scans_every_text_file_it_ships(self):
        # A value hiding in a file type nobody thought to scan is the whole risk;
        # the macOS launcher was exactly that gap.
        source = Path(M.__file__).read_text(encoding="utf-8")
        for suffix in (".py", ".yaml", ".txt", ".html", ".js", ".bat", ".command"):
            assert f'"{suffix}"' in source, f"{suffix} files are not scanned"

    def test_it_checks_every_folder_that_could_hold_client_data(self):
        source = Path(M.__file__).read_text(encoding="utf-8")
        for folder in ("output", "trash", "feedback", "exports"):
            assert f'"{folder}"' in source

    def test_a_marker_found_in_the_output_is_a_hard_failure(self, tmp_path):
        # Whatever the mechanism, the build must not be able to finish while a
        # known-private value is sitting in the copy.
        source = Path(M.__file__).read_text(encoding="utf-8")
        assert "private_markers" in source
        assert "sys.exit" in source or "raise SystemExit" in source
