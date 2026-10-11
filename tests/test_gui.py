"""GUI smoke tests. They need tkinter and a display (xvfb-run on Linux CI); otherwise skipped."""

from __future__ import annotations

import time
import zipfile

import pytest

tk = pytest.importorskip("tkinter")

from riftunpack import gui  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    root.withdraw()
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    application = gui.RiftUnpackApp(root)
    application.destination.set(str(tmp_path / "out"))
    yield application
    root.destroy()


def _drain(app, timeout=10.0):
    """Let Tk's own event loop run (the GUI polls its queue) until the worker is done."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.root.update()
        if str(app.extract_button.cget("state")) == "normal":
            return
        time.sleep(0.02)
    raise AssertionError("worker did not finish")


def test_window_is_created_with_title_and_disabled_cancel(app):
    assert app.root.title() == "RiftUnpack"
    assert str(app.cancel_button.cget("state")) == "disabled"
    assert str(app.extract_button.cget("state")) == "normal"


def test_add_filters_unsupported_files_and_duplicates(app, tmp_path):
    good = tmp_path / "game.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("a.txt", "a")
    junk = tmp_path / "notes.txt"
    junk.write_text("hello")
    app._add([good, junk, good])
    assert app.files == [good]
    assert "1 archive(s)" in app.file_label.cget("text")
    app.clear()
    assert app.files == []


def test_start_without_files_does_not_create_job(app, monkeypatch):
    asked = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: asked.append(a))
    app.start()
    assert asked
    assert app.job is None


def test_extract_end_to_end_through_gui(app, tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("data/mod.txt", "hello")
    app._add([archive])
    app.start()
    assert str(app.extract_button.cget("state")) == "disabled"
    _drain(app)
    assert (tmp_path / "out" / "pack" / "data" / "mod.txt").read_text() == "hello"
    assert str(app.extract_button.cget("state")) == "normal"


def test_cancel_sets_job_flag(app, tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.txt", "a")
    app._add([archive])
    app.start()
    app.cancel()
    assert app.job.cancel_event.is_set()
    _drain(app)


def test_password_prompt_answer_reaches_job(app, tmp_path, monkeypatch):
    from riftunpack import jobs
    from riftunpack.engine import ExtractionReport
    from riftunpack.errors import PasswordRequiredError

    archive = tmp_path / "locked.zip"
    archive.write_bytes(b"")
    monkeypatch.setattr(gui.simpledialog, "askstring", lambda *a, **k: "pw")
    calls = []

    def fake(path, destination, options, **kwargs):
        calls.append(options.password)
        if options.password != "pw":
            raise PasswordRequiredError("need")
        return ExtractionReport(path, format="zip", destination=destination, extracted=1)

    app.files = [archive]
    app.job = jobs.ExtractionJob(app.files, app.destination.get(), extract=fake)
    import threading

    worker = threading.Thread(target=app.job.run, args=(app.events.put,))
    app.extract_button.configure(state="disabled")
    worker.start()
    _drain(app)
    worker.join(timeout=5)
    assert calls == [None, "pw"]


def test_main_falls_back_to_plain_tk_when_tkdnd_cannot_load(monkeypatch):
    """A broken tkinterdnd2 install must not stop the app from starting."""
    tkinterdnd2 = pytest.importorskip("tkinterdnd2")
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display available for tkinter")
    created = []

    def broken_tk(*args, **kwargs):
        raise RuntimeError("Unable to load tkdnd library.")

    monkeypatch.setattr(tkinterdnd2.TkinterDnD, "Tk", broken_tk)
    monkeypatch.setattr(tk.Tk, "mainloop", lambda self, n=0: None)
    monkeypatch.setattr(gui, "RiftUnpackApp", lambda root: created.append(root))
    gui.main()
    assert len(created) == 1
    assert type(created[0]) is tk.Tk
    created[0].destroy()
