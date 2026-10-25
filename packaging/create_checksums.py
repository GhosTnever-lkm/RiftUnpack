"""Create a deterministic SHA-256SUMS-style manifest for release files."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Sequence


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(paths: Sequence[Path]) -> str:
    if not paths:
        raise ValueError("provide at least one file")
    if len({path.name for path in paths}) != len(paths):
        raise ValueError("release filenames must be unique")

    entries = []
    for path in sorted(paths, key=lambda item: item.name.casefold()):
        if not path.is_file():
            raise FileNotFoundError(f"release file not found: {path}")
        entries.append(f"{digest_file(path)}  {path.name}")
    return "\n".join(entries) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = create_manifest(args.files)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(manifest, encoding="ascii", newline="\n")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
