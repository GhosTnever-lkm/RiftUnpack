"""Tests for the tkinter-free extraction queue used by the GUI."""

from __future__ import annotations

import threading
from pathlib import Path

from riftunpack.engine import ExtractionReport
from riftunpack.errors import CancelledError, PasswordRequiredError, RiftUnpackError
from riftunpack.jobs import ExtractionJob


def _report(archive: Path, destination: Path) -> ExtractionReport:
    return ExtractionReport(
        archive=archive,
        format="zip",
        destination=destination,
        extracted=1,
        bytes_written=1,
        warnings=[],
    )


class Recorder:
    def __init__(self):
        self.events: list[tuple] = []

    def __call__(self, event: tuple) -> None:
        self.events.append(event)

    def kinds(self) -> list[str]:
        return [e[0] for e in self.events]

    def logs(self) -> list[str]:
        return [e[1] for e in self.events if e[0] == "log"]


def test_extracts_all_archives_in_order_and_reports_done(tmp_path):
    seen = []

    def fake(archive, destination, options, progress=None, cancel=None, **_):
        seen.append((archive.name, destination.name, options.password))
        return _report(archive, destination)

    job = ExtractionJob([tmp_path / "a.zip", tmp_path / "b.7z"], tmp_path / "out", extract=fake)
    rec = Recorder()
    job.run(rec)
    assert seen == [("a.zip", "a", None), ("b.7z", "b", None)]
    assert rec.kinds()[-1] == "done"
    assert rec.logs()[0].startswith("✓ a.zip: 1 files")


def test_error_in_one_archive_does_not_stop_queue(tmp_path):
    def fake(archive, destination, options, **_):
        if archive.name == "bad.zip":
            raise RiftUnpackError("broken")
        return _report(archive, destination)

    rec = Recorder()
    ExtractionJob([tmp_path / "bad.zip", tmp_path / "ok.zip"], tmp_path, extract=fake).run(rec)
    logs = rec.logs()
    assert "✗ bad.zip: broken" in logs
    assert any(line.startswith("✓ ok.zip") for line in logs)


def test_os_error_is_reported_not_raised(tmp_path):
    def fake(archive, destination, options, **_):
        raise OSError("disk full")

    rec = Recorder()
    ExtractionJob([tmp_path / "x.zip"], tmp_path, extract=fake).run(rec)
    assert "✗ x.zip: disk full" in rec.logs()
    assert rec.kinds()[-1] == "done"


def test_cancel_before_start_runs_nothing(tmp_path):
    calls = []

    def fake(archive, destination, options, **_):
        calls.append(archive)
        return _report(archive, destination)

    job = ExtractionJob([tmp_path / "a.zip", tmp_path / "b.zip"], tmp_path, extract=fake)
    job.cancel()
    rec = Recorder()
    job.run(rec)
    assert calls == []
    assert rec.kinds() == ["done"]


def test_cancel_raised_by_engine_stops_remaining_queue(tmp_path):
    calls = []

    def fake(archive, destination, options, **_):
        calls.append(archive.name)
        raise CancelledError("stop")

    rec = Recorder()
    ExtractionJob([tmp_path / "a.zip", tmp_path / "b.zip"], tmp_path, extract=fake).run(rec)
    assert calls == ["a.zip"]
    assert "Cancelled." in rec.logs()
    assert rec.kinds()[-1] == "done"


def test_cancel_while_waiting_for_password(tmp_path):
    def fake(archive, destination, options, **_):
        raise PasswordRequiredError("need password")

    job = ExtractionJob([tmp_path / "locked.zip", tmp_path / "next.zip"], tmp_path, extract=fake)
    rec = Recorder()
    worker = threading.Thread(target=job.run, args=(rec,))
    worker.start()
    # Wait until the job asks for a password, then cancel instead of answering.
    for _ in range(500):
        if "password_needed" in rec.kinds():
            break
        threading.Event().wait(0.01)
    job.cancel()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert rec.kinds().count("password_needed") == 1
    assert "Cancelled." in rec.logs()
    assert rec.kinds()[-1] == "done"


def test_password_answer_is_used_and_kept_for_next_archive(tmp_path):
    attempts = []

    def fake(archive, destination, options, **_):
        attempts.append((archive.name, options.password))
        if options.password != "secret":
            raise PasswordRequiredError("need password")
        return _report(archive, destination)

    job = ExtractionJob([tmp_path / "a.zip", tmp_path / "b.zip"], tmp_path, extract=fake)
    rec = Recorder()
    job.answer_password("secret")  # answer queued before the prompt is raised
    job.run(rec)
    assert attempts == [("a.zip", None), ("a.zip", "secret"), ("b.zip", "secret")]
    assert "✓ b.zip: 1 files" in " ".join(rec.logs())


def test_wrong_password_prompts_again(tmp_path):
    def fake(archive, destination, options, **_):
        if options.password != "right":
            raise PasswordRequiredError("wrong")
        return _report(archive, destination)

    job = ExtractionJob([tmp_path / "a.zip"], tmp_path, password="wrong", extract=fake)
    job.answer_password("right")
    rec = Recorder()
    job.run(rec)
    assert rec.kinds().count("password_needed") == 1
    assert any("incorrect password" in line for line in rec.logs())
    assert any(line.startswith("✓ a.zip") for line in rec.logs())


def test_skipping_password_leaves_archive_unextracted(tmp_path):
    def fake(archive, destination, options, **_):
        raise PasswordRequiredError("need password")

    job = ExtractionJob([tmp_path / "locked.zip"], tmp_path, extract=fake)
    job.answer_password(None)
    rec = Recorder()
    job.run(rec)
    assert "Skipped locked.zip; no password entered." in rec.logs()
    assert rec.kinds()[-1] == "done"


def test_empty_password_answer_counts_as_skip(tmp_path):
    def fake(archive, destination, options, **_):
        raise PasswordRequiredError("need password")

    job = ExtractionJob([tmp_path / "locked.zip"], tmp_path, extract=fake)
    job.answer_password("")
    rec = Recorder()
    job.run(rec)
    assert "Skipped locked.zip; no password entered." in rec.logs()


def test_progress_events_carry_queue_position(tmp_path):
    def fake(archive, destination, options, progress=None, **_):
        progress(1, 2, "inner.txt")
        return _report(archive, destination)

    rec = Recorder()
    ExtractionJob([tmp_path / "a.zip", tmp_path / "b.zip"], tmp_path, extract=fake).run(rec)
    progress = [e for e in rec.events if e[0] == "progress"]
    assert progress == [
        ("progress", 1, 2, 1, 2, "inner.txt"),
        ("progress", 2, 2, 1, 2, "inner.txt"),
    ]


def test_default_password_is_none_for_empty_string(tmp_path):
    job = ExtractionJob([], tmp_path, password="")
    assert job.password is None
