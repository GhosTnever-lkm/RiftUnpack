"""Tkinter-free extraction queue used by the GUI (and testable without a display).

The GUI owns a :class:`ExtractionJob`, runs :meth:`ExtractionJob.run` in a worker thread and
receives plain-tuple events through an ``emit`` callable:

* ``("log", text)``
* ``("progress", index, total, current, total_units, name)``
* ``("password_needed", archive_name)``
* ``("done",)``
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from .detect import archive_base_name
from .engine import ExtractionReport, ExtractOptions, extract_archive
from .errors import CancelledError, PasswordRequiredError, RiftUnpackError

Emit = Callable[[tuple], None]
ExtractFn = Callable[..., ExtractionReport]

PASSWORD_POLL_SECONDS = 0.2


class ExtractionJob:
    """Extract a list of archives one by one, asking for passwords when needed."""

    def __init__(
        self,
        archives: Iterable[Path | str],
        destination: Path | str,
        *,
        password: str | None = None,
        extract: ExtractFn = extract_archive,
    ) -> None:
        self.archives = [Path(a) for a in archives]
        self.destination = Path(destination)
        self.password = password or None
        self.cancel_event = threading.Event()
        self.password_replies: queue.Queue = queue.Queue()
        self._extract = extract

    # -- controls called from the UI thread -------------------------------------------------
    def cancel(self) -> None:
        self.cancel_event.set()

    def answer_password(self, value: str | None) -> None:
        """Deliver the user's answer to a ``password_needed`` event (``None`` = skip)."""
        self.password_replies.put(value)

    # -- worker -----------------------------------------------------------------------------
    def run(self, emit: Emit) -> None:
        total = len(self.archives)
        try:
            for index, archive in enumerate(self.archives, 1):
                if self.cancel_event.is_set():
                    break
                if not self._extract_one(index, total, archive, emit):
                    break
        finally:
            emit(("done",))

    def _extract_one(self, index: int, total: int, archive: Path, emit: Emit) -> bool:
        """Return False when the whole queue must stop (cancelled)."""
        target = self.destination / archive_base_name(archive)
        while True:
            try:
                report = self._extract(
                    archive,
                    target,
                    ExtractOptions(password=self.password),
                    progress=self._progress_sink(index, total, emit),
                    cancel=self.cancel_event.is_set,
                )
            except CancelledError:
                emit(("log", "Cancelled."))
                return False
            except PasswordRequiredError:
                if self.password is not None:
                    emit(("log", f"✗ {archive.name}: incorrect password."))
                    self.password = None
                answer = self._ask_password(archive, emit)
                if answer is _CANCELLED:
                    emit(("log", "Cancelled."))
                    return False
                if answer is None:
                    emit(("log", f"Skipped {archive.name}; no password entered."))
                    return True
                self.password = answer
                continue
            except (RiftUnpackError, OSError) as exc:
                emit(("log", f"✗ {archive.name}: {exc}"))
                return True
            emit(("log", f"✓ {archive.name}: {report.extracted} files → {target}"))
            return True

    def _ask_password(self, archive: Path, emit: Emit):
        """Return the entered password, ``None`` for skip, or ``_CANCELLED``."""
        emit(("password_needed", archive.name))
        while True:
            if self.cancel_event.is_set():
                return _CANCELLED
            try:
                return self.password_replies.get(timeout=PASSWORD_POLL_SECONDS) or None
            except queue.Empty:
                continue

    @staticmethod
    def _progress_sink(index: int, total: int, emit: Emit):
        def progress(current, units, name):
            emit(("progress", index, total, current, units, name))

        return progress


_CANCELLED = object()
