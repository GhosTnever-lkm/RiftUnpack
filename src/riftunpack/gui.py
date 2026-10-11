"""Tkinter GUI; drag-and-drop is enabled when tkinterdnd2 is installed."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .detect import detect_format
from .engine import ExtractOptions, extract_archive
from .errors import CancelledError, PasswordRequiredError, RiftUnpackError


class RiftUnpackApp:
    """Small responsive GUI for extracting one or more local archives."""

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("RiftUnpack")
        root.geometry("680x490")
        root.minsize(540, 380)
        self.files: list[Path] = []
        self.events: queue.Queue = queue.Queue()
        self.password_reply: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.destination = tk.StringVar(value=str(Path.home() / "Extracted"))
        self.password: str | None = None
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        ttk.Label(root, text="RiftUnpack", font=("Segoe UI", 22, "bold")).pack(pady=(14, 2))
        ttk.Label(root, text="Fast · Safe · Offline", foreground="#687387").pack(pady=(0, 10))
        self.drop = tk.Label(
            root,
            text="Drop archives here or click Add files",
            bg="#eef2f7",
            fg="#334155",
            relief="groove",
            height=3,
            font=("Segoe UI", 12),
        )
        self.drop.pack(fill="x", padx=18, pady=4)
        try:
            from tkinterdnd2 import DND_FILES

            root.drop_target_register(DND_FILES)
            root.dnd_bind("<<Drop>>", self._on_drop)
            self.drop.configure(text="Drop archives here or use Add files")
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self._on_drop)
        except ImportError:
            self.drop.configure(text="Drag-and-drop needs optional tkinterdnd2; use Add files")
        buttons = ttk.Frame(root)
        buttons.pack(fill="x", padx=18, pady=6)
        ttk.Button(buttons, text="Add files…", command=self.add_files).pack(side="left")
        ttk.Button(buttons, text="Add folder…", command=self.add_folder).pack(side="left", padx=7)
        ttk.Button(buttons, text="Clear", command=self.clear).pack(side="left")
        self.file_label = ttk.Label(root, text="No archives selected", wraplength=620)
        self.file_label.pack(fill="x", padx=20, pady=2)
        dest = ttk.Frame(root)
        dest.pack(fill="x", padx=18, pady=5)
        ttk.Label(dest, text="Destination").pack(side="left")
        ttk.Entry(dest, textvariable=self.destination).pack(
            side="left", fill="x", expand=True, padx=8
        )
        ttk.Button(dest, text="Browse…", command=self.choose_destination).pack(side="right")
        self.progress = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=18, pady=(8, 4))
        actions = ttk.Frame(root)
        actions.pack(fill="x", padx=18, pady=4)
        self.extract_button = ttk.Button(actions, text="Extract", command=self.start)
        self.extract_button.pack(side="left")
        self.cancel_button = ttk.Button(
            actions, text="Cancel", command=self.cancel, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(actions, text="Password…", command=self.set_password).pack(side="left")
        self.log = tk.Text(root, height=10, wrap="word", state="disabled", bg="#f8fafc")
        self.log.pack(fill="both", expand=True, padx=18, pady=(4, 14))
        self.root.after(100, self._poll)

    def add_files(self):
        self._add([Path(x) for x in filedialog.askopenfilenames(title="Choose archives")])

    def add_folder(self):
        name = filedialog.askdirectory(title="Choose folder")
        if name:
            self._add([p for p in Path(name).rglob("*") if p.is_file() and detect_format(p)])

    def _on_drop(self, event):
        try:
            self._add([Path(x) for x in self.root.tk.splitlist(event.data)])
        except tk.TclError:
            pass

    def _add(self, paths):
        self.files = list(
            dict.fromkeys([*self.files, *(p for p in paths if p.is_file() and detect_format(p))])
        )
        self.file_label.configure(
            text=f"{len(self.files)} archive(s): " + ", ".join(p.name for p in self.files[:5])
        )
        if not self.files:
            self.file_label.configure(text="No supported archives selected")

    def clear(self):
        self.files = []
        self.file_label.configure(text="No archives selected")

    def choose_destination(self):
        name = filedialog.askdirectory(
            title="Choose output folder", initialdir=self.destination.get()
        )
        if name:
            self.destination.set(name)

    def set_password(self):
        value = simpledialog.askstring(
            "Archive password",
            "Enter the archive password. It is used for this session only:",
            show="*",
            parent=self.root,
        )
        if value is not None:
            self.password = value or None
            self._write("Password set for this GUI session." if value else "Password cleared.")

    def start(self):
        if not self.files:
            messagebox.showinfo("RiftUnpack", "Add at least one recognized archive first.")
            return
        self.cancel_event.clear()
        self.extract_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self._write(f"Starting {len(self.files)} archive(s)…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        base = Path(self.destination.get())
        for index, archive in enumerate(self.files, 1):
            if self.cancel_event.is_set():
                break
            target = base / archive.stem
            total_files = len(self.files)
            try:
                report = extract_archive(
                    archive,
                    target,
                    ExtractOptions(password=self.password),
                    progress=lambda c, t, n, i=index, total_files=total_files: self.events.put(
                        ("progress", i, total_files, c, t, n)
                    ),
                    cancel=self.cancel_event.is_set,
                )
                self.events.put(("log", f"✓ {archive.name}: {report.extracted} files → {target}"))
            except PasswordRequiredError:
                self.events.put(("password_needed", archive.name))
                password = None
                answered = False
                while not self.cancel_event.is_set() and not answered:
                    try:
                        password = self.password_reply.get(timeout=0.2)
                        answered = True
                    except queue.Empty:
                        continue
                if password:
                    self.password = password
                    try:
                        report = extract_archive(
                            archive,
                            target,
                            ExtractOptions(password=password),
                            cancel=self.cancel_event.is_set,
                        )
                        self.events.put(
                            ("log", f"✓ {archive.name}: {report.extracted} files → {target}")
                        )
                    except (RiftUnpackError, OSError) as exc:
                        self.events.put(("log", f"✗ {archive.name}: {exc}"))
                elif answered:
                    self.events.put(("log", f"Skipped {archive.name}; no password entered."))
            except CancelledError:
                self.events.put(("log", "Cancelled."))
                break
            except (RiftUnpackError, OSError) as exc:
                self.events.put(("log", f"✗ {archive.name}: {exc}"))
        self.events.put(("done",))

    def cancel(self):
        self.cancel_event.set()
        self._write("Cancellation requested…")

    def _write(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    self._write(event[1])
                elif event[0] == "password_needed":
                    value = simpledialog.askstring(
                        "Archive password",
                        f"Enter the password for {event[1]}:",
                        show="*",
                        parent=self.root,
                    )
                    self.password_reply.put(value)
                elif event[0] == "progress":
                    _, i, n, current, total, name = event
                    self.progress["value"] = ((i - 1) + current / max(total, 1)) * 100 / n
                elif event[0] == "done":
                    self.extract_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.progress["value"] = 0
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def main() -> None:
    try:
        from tkinterdnd2 import TkinterDnD

        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    RiftUnpackApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
