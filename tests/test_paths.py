"""Path safety. Every function here decides where files get written or deleted."""
from __future__ import annotations

import pytest

from paths import (OUTPUT_DIR, client_output_dir, contains, resolve,
                   safe_dir_name, slugify)


class TestSlugify:
    @pytest.mark.parametrize("value,expected", [
        ("Fabian, Noor", "fabian_noor"),
        ("FABIAN  NOOR", "fabian_noor"),
        ("Ann-Marie O'Brien", "ann_marie_o_brien"),
        ("", "client"),
        (None, "client"),
    ])
    def test_normalizes(self, value, expected):
        assert slugify(value) == expected

    def test_can_return_empty_which_is_why_callers_must_not_build_paths_from_it(self):
        # The reason safe_dir_name exists. OUTPUT_DIR / "" is OUTPUT_DIR.
        assert slugify("...") == ""
        assert slugify("!!!") == ""


class TestSafeDirName:
    @pytest.mark.parametrize("value", ["...", "!!!", "", None, "   ", ". ", "///"])
    def test_never_empty(self, value):
        assert safe_dir_name(value)

    @pytest.mark.parametrize("value", ["con", "CON", "nul", "com1", "lpt9"])
    def test_avoids_windows_device_names(self, value):
        assert safe_dir_name(value).lower() not in {
            "con", "prn", "aux", "nul", "com1", "lpt9"}

    def test_keeps_a_readable_name_as_it_is(self):
        assert safe_dir_name("Noor F") == "Noor F"
        assert safe_dir_name("Smith John (2)") == "smith_john_2"   # falls back to slug

    def test_strips_trailing_dot_and_space_windows_rejects_both(self):
        assert not safe_dir_name("Smith. ").endswith((".", " "))


class TestClientOutputDir:
    @pytest.mark.parametrize("value", ["...", "", None, "   "])
    def test_never_resolves_to_the_output_root(self, value):
        # Deleting a client whose folder resolved to output/ itself used to move
        # every OTHER client's paperwork to trash along with it.
        assert client_output_dir(value).resolve() != OUTPUT_DIR.resolve()

    def test_stays_inside_output(self):
        assert contains(OUTPUT_DIR, client_output_dir("../../etc/passwd"))

    def test_distinct_names_give_distinct_folders(self):
        assert client_output_dir("Smith John") != client_output_dir("Smith John 2")


class TestContains:
    def test_rejects_traversal(self):
        assert not contains(OUTPUT_DIR, OUTPUT_DIR / ".." / "clients")

    def test_accepts_a_child(self):
        assert contains(OUTPUT_DIR, OUTPUT_DIR / "someone" / "form.pdf")

    def test_a_directory_does_not_contain_itself_for_our_purposes(self):
        # contains() is True here; callers that MOVE a path must also check equality,
        # which is what client_output_dir and deletion_preview do.
        assert contains(OUTPUT_DIR, OUTPUT_DIR)


def test_resolve_anchors_relative_paths_to_root():
    assert resolve("forms/mappings").is_absolute()
    assert resolve(OUTPUT_DIR) == OUTPUT_DIR          # absolute is left alone
