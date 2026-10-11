# Contributing to RiftUnpack

Thanks for helping! RiftUnpack focuses on safe, offline extraction for players and modders.

## Development setup

```bash
python -m pip install -e ".[dev,all-formats]"
ruff check .
pytest --cov=riftunpack --cov-report=term-missing
```

Python 3.10–3.12 are supported in CI; please run tests on at least one of them.

## Guidelines

- Keep changes small and focused; one feature or fix per pull request.
- Every extraction change needs a test. Security-relevant changes (paths, symlinks, limits, passwords) need a regression test that fails before the fix.
- Never weaken path validation, size/ratio limits or symlink rules without a clear justification in the PR description.
- Do not add network calls or telemetry. RiftUnpack works offline.
- Do not add password guessing or cracking features.

## Reporting bugs

Use the bug report template and include the archive format, the command or GUI steps, and the full error text. If the archive is private, describe its structure instead of attaching it.
