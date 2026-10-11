# RiftUnpack — STATE

## Plan
1. [x] Extraction core: signature-based format detection, optional backend discovery, safe path handling, archive-bomb limits, conflict policy, password support, metadata preservation, multipart hints.
2. [x] CLI: extraction, recursive discovery, member masks, listing, integrity checks, nested extraction, parallel batches, Windows context-menu scripts.
3. [x] Tkinter GUI: drag and drop (when `tkinterdnd2` is installed), destination selection, password prompt, progress, log, cancellation.
4. [x] Tests and lint: pytest with generated archives/fixtures; Ruff.
5. [~] Docs, CI, and v1.0.0 release: README, LICENSE, CHANGELOG, GitHub Actions workflow are written. Public GitHub repository is NOT created yet (see Blockers).

## Verified so far
- Python 3.13: `pytest` — 23 passed; `ruff check .` — all checks passed; coverage around 52%.
- Covered: ZIP/TAR and compressed streams, 7z with password, encodings, path traversal, symlinks, archive-bomb limits, nested archives, cancellation.
- PyInstaller CLI (`RiftUnpack`, Linux build, ~55 MB): `--version` and `--help` work; ZIP extraction smoke test works.
- PyInstaller GUI (`RiftUnpack-GUI`) could not be built in this Linux sandbox: the Python here has no `tkinter`. The Windows CI job (windows-latest) is the expected place to build it.

## Blockers
- GitHub: the connected GitHub token returns `403 Resource not accessible by personal access token` for `POST /user/repos`, so the public repository `GhosTnever-lkm/RiftUnpack` cannot be created from here. No existing RiftUnpack repository was found in the account (58 public repos checked).
- Needed to continue: either create an empty public repository `RiftUnpack` manually, or grant the token repository-creation and contents-write permissions.
- Release v1.0.0 is created only by the Actions workflow after the checks pass, using the repository's `GITHUB_TOKEN` (`contents: write`).

## Next steps
1. Create or unlock the public `GhosTnever-lkm/RiftUnpack` repository.
2. Push the local commit to `main`.
3. Confirm the CI run on Ubuntu/Windows x Python 3.10–3.12 is green.
4. Confirm the v1.0.0 release has `RiftUnpack.exe` and `RiftUnpack-GUI.exe`.

## Known backend boundary
Built-in ZIP/TAR/compressed-TAR support is offline and has no external runtime requirement. 7z requires `py7zr` or 7-Zip; RAR needs a compatible system tool; ZST needs `zstandard`; ISO/CAB need `libarchive-c` or 7-Zip. The app reports actionable installation guidance when a backend is missing. No password guessing is performed.
