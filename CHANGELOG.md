# Changelog

## [1.0.1] - 2026-10-11

### Fixed
- ISO and CAB archives read through libarchive crashed on every entry (libarchive-c exposes `isdir`, `isreg`, `issym`, `islnk` as properties); ISO root entries are now skipped.
- RAR error handling referenced a non-existent `rarfile.RarError`; it now uses `rarfile.Error`.
- Split 7-Zip sets (`archive.7z.001`) and split ZIP sets (`archive.zip.001`) are read through 7-Zip instead of failing.
- ZIP files that use AES or other methods unsupported by Python's zipfile fall back to 7-Zip; without 7-Zip the error explains what to install.
- 7-Zip output is written to temporary files: a large listing could previously block the extractor forever.
- 7-Zip runs with a closed stdin, so it never waits for an interactive password prompt.
- With `--include`, the external 7-Zip path could delete every extracted file.
- If py7zr fails on a 7z archive, 7-Zip is tried before giving up.
- Corrupt TAR data raises a RiftUnpackError instead of an internal exception.
- `.tar.gz`, `.tar.bz2`, `.tar.xz` archives extract to `name/` (not `name.tar/`); split volumes (`.7z.001`, `.part1.rar`) use the base name too.
- CLI: archive and member names that look like Rich markup (`[bold]`) are printed literally.
- CLI: an unexpected error in one archive is reported and the rest of the batch continues.
- GUI: a wrong password asks again instead of failing the archive; the queue, cancel, and password logic is tested without a display.
- GUI: the app starts with a plain window when `tkinterdnd2` is installed but its native tkdnd library cannot load (previously a crash at startup).
- ZIP: `metadata_encoding` is only passed on Python 3.11+ (checked by version, not by catching TypeError).

### Added
- `riftunpack.jobs`: tkinter-free extraction queue used by the GUI (cancel, password answers, progress events).
- Tests: real-file coverage for ZIP (incl. ZipCrypto and AES via 7-Zip), TAR family, GZ/BZ2/XZ/ZST, 7z (incl. password and split volumes), ISO, legacy encodings (cp866, cp1251, Shift-JIS), zip slip, symlinks, bombs, limits, nested archives, cancel, and GUI/job tests (run under Xvfb on Linux CI).
- CI: mypy, 80% coverage floor, 7-Zip/libarchive/Xvfb on Linux, 7-Zip on PATH on Windows, and a Windows executable smoke test (`packaging/smoke_test_exe.py`) that runs before any release is published.
- Release tags follow the package version (`v1.0.1`) instead of a fixed `v1.0.0`.
- Real GUI screenshot in the README (replaces the drawn preview).

### Changed
- README: formats table states what is tested and what is experimental; the unverified "Pro" section was removed; examples were run against fixtures.
- Drag-and-drop is described as a standard dependency (it was described as optional).

## [1.0.0] - 2026-10-11

- Treat ZIP entries with zero/unspecified permission bits as readable defaults instead of applying mode `000`; legacy encoded filenames now copy successfully on POSIX.

### Added
- Offline-first CLI and Tkinter interface for archive extraction.
- Signature-based detection, ZIP/TAR/compressed streams, optional 7z/RAR and 7-Zip backends.
- Path traversal and link protections, configurable archive limits, conflict policies, nested extraction, filename encoding override, and parallel CLI batches.
- Windows Explorer context-menu installer, tests, CI, and PyInstaller release workflow.
