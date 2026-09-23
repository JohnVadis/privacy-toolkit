"""Retention for the two folders that accumulate client data as a side effect."""
from __future__ import annotations

import os
import time

import pytest

import housekeeping as HK
import paths


def aged(path, days: float):
    """Backdate something so retention can be tested without waiting."""
    when = time.time() - days * 86400
    os.utime(path, (when, when))
    return path


def write_aged(path, days: float, content: bytes = b"x"):
    """Create a file and backdate it in one step."""
    path.write_bytes(content)
    return aged(path, days)


@pytest.fixture
def folders():
    HK.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    paths.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    yield paths.TRASH_DIR, HK.FEEDBACK_DIR
    import shutil

    shutil.rmtree(HK.FEEDBACK_DIR, ignore_errors=True)


class TestStatus:
    def test_reports_nothing_for_empty_folders(self, folders):
        report = HK.status()
        assert report["trash"]["items"] == 0
        assert report["feedback"]["items"] == 0

    def test_counts_items_and_bytes(self, folders):
        trash, feedback = folders
        (feedback / "comment_a.png").write_bytes(b"x" * 100)
        (feedback / "comment_b.png").write_bytes(b"x" * 50)
        assert HK.status()["feedback"]["items"] == 2
        assert HK.status()["feedback"]["bytes"] == 150

    def test_measures_a_deleted_client_folder_whole(self, folders):
        trash, _ = folders
        case = trash / "2026-01-01_000000__someone"
        (case / "output").mkdir(parents=True)
        (case / "someone.yaml").write_bytes(b"x" * 10)
        (case / "output" / "form.pdf").write_bytes(b"x" * 90)
        assert HK.status()["trash"]["bytes"] == 100

    def test_flags_what_is_past_its_date(self, folders):
        trash, feedback = folders
        write_aged(feedback / "old.png", 40)
        (feedback / "new.png").write_bytes(b"x")
        stale = HK.status()["feedback"]["stale"]
        assert [r["name"] for r in stale] == ["old.png"]

    def test_reports_the_age_of_the_oldest(self, folders):
        trash, feedback = folders
        write_aged(feedback / "old.png", 40)
        assert HK.status()["feedback"]["oldest_days"] == pytest.approx(40, abs=1)


class TestPrune:
    def test_removes_only_what_is_past_the_limit(self, folders):
        trash, feedback = folders
        write_aged(feedback / "old.png", 40)
        (feedback / "new.png").write_bytes(b"x")

        removed = HK.prune(feedback, HK.FEEDBACK_DAYS)
        assert [r["name"] for r in removed] == ["old.png"]
        assert not (feedback / "old.png").exists()
        assert (feedback / "new.png").exists()

    def test_removes_a_whole_deleted_client_folder(self, folders):
        trash, _ = folders
        case = trash / "2026-01-01_000000__someone"
        case.mkdir(parents=True)
        (case / "someone.yaml").write_bytes(b"x")
        aged(case, 200)

        HK.prune(trash, HK.TRASH_DAYS)
        assert not case.exists()

    def test_a_dry_run_removes_nothing(self, folders):
        trash, feedback = folders
        write_aged(feedback / "old.png", 40)

        removed = HK.prune(feedback, HK.FEEDBACK_DAYS, dry_run=True)
        assert [r["name"] for r in removed] == ["old.png"]
        assert (feedback / "old.png").exists()

    def test_it_never_removes_the_folder_itself(self, folders):
        trash, feedback = folders
        aged(feedback, 999)
        HK.prune(feedback, HK.FEEDBACK_DAYS)
        assert feedback.is_dir()

    def test_an_absent_folder_is_not_an_error(self, tmp_path):
        assert HK.prune(tmp_path / "never_existed", 30) == []

    def test_a_longer_retention_keeps_more(self, folders):
        trash, feedback = folders
        write_aged(feedback / "old.png", 40)
        assert HK.prune(feedback, 90) == []

    def test_trash_is_kept_longer_than_feedback_by_default(self):
        # Restoring a deleted client is the whole reason trash exists.
        assert HK.TRASH_DAYS > HK.FEEDBACK_DAYS


class TestItOnlyRunsWhenAsked:
    def test_importing_the_module_removes_nothing(self, folders):
        trash, feedback = folders
        write_aged(feedback / "ancient.png", 9999)
        import importlib

        importlib.reload(HK)
        assert (feedback / "ancient.png").exists()

    def test_the_report_alone_removes_nothing(self, folders):
        trash, feedback = folders
        write_aged(feedback / "ancient.png", 9999)
        HK.status()
        assert (feedback / "ancient.png").exists()
