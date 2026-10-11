# RiftUnpack — STATE

## Plan
1. [x] Extraction core: signature-based format detection, optional backend discovery, safe path handling, archive-bomb limits, conflict policy, password support, metadata preservation, multipart hints.
2. [x] CLI: extraction, recursive discovery, member masks, listing, integrity checks, nested extraction, parallel batches, Windows context-menu scripts.
3. [x] Tkinter GUI: drag and drop (when `tkinterdnd2` is installed), destination selection, password prompt, progress, log, cancellation.
4. [x] Tests and lint: pytest with generated archives/fixtures; Ruff.
5. [x] Docs, CI, and v1.0.0 release: README, LICENSE, CHANGELOG, templates, GitHub Actions workflow; repository published and CI green.

## Verified so far
- Python 3.13: `pytest` — 23 passed; `ruff check .` — all checks passed; coverage around 52%.
- Covered: ZIP/TAR and compressed streams, 7z with password, encodings, path traversal, symlinks, archive-bomb limits, nested archives, cancellation.
- PyInstaller CLI (`RiftUnpack`, Linux build, ~55 MB): `--version` and `--help` work; ZIP extraction smoke test works.
- PyInstaller GUI (`RiftUnpack-GUI`) could not be built in this Linux sandbox: the Python here has no `tkinter`. The Windows release job builds it.

## Publication
- Repository: https://github.com/GhosTnever-lkm/RiftUnpack (public, main).
- The GitHub connector token cannot create repositories or push contents (403). The repository was created in the browser and the files were uploaded through the web UI.
- CI "Tests, lint, and v1 release": green on the latest commit (checks on ubuntu/windows x Python 3.10–3.12, then release job).
- Release v1.0.0 published by the workflow: https://github.com/GhosTnever-lkm/RiftUnpack/releases/tag/v1.0.0
- Commit 924936a (`fix: preserve readable defaults for ZIP entries without mode`) was added to main by someone else; it is kept as is.

## Next steps
1. Verify that the release contains RiftUnpack.exe and RiftUnpack-GUI.exe.
2. Add repository topics from README.
3. Run the Windows GUI smoke test on a Windows machine (tkinter is not available in this Linux sandbox).

## Known backend boundary
Built-in ZIP/TAR/compressed-TAR support is offline and has no external runtime requirement. 7z requires `py7zr` or 7-Zip; RAR needs a compatible system tool; ZST needs `zstandard`; ISO/CAB need `libarchive-c` or 7-Zip. The app reports actionable installation guidance when a backend is missing. No password guessing is performed.
