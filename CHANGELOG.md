# Changelog

## [1.0.0] - 2026-10-11

- Treat ZIP entries with zero/unspecified permission bits as readable defaults instead of applying mode `000`; legacy encoded filenames now copy successfully on POSIX.

### Added
- Offline-first CLI and Tkinter interface for archive extraction.
- Signature-based detection, ZIP/TAR/compressed streams, optional 7z/RAR and 7-Zip backends.
- Path traversal and link protections, configurable archive limits, conflict policies, nested extraction, filename encoding override, and parallel CLI batches.
- Windows Explorer context-menu installer, tests, CI, and PyInstaller release workflow.
