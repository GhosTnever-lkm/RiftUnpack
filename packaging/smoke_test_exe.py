"""Smoke test for the built Windows executables (run by the release workflow).

Usage: python packaging/smoke_test_exe.py dist/RiftUnpack.exe dist/RiftUnpack-GUI.exe

The CLI is exercised on real ZIP and 7z fixtures. The 7z fixture is extracted by the bundled
py7zr, so run this on a machine without 7-Zip on PATH to prove the frozen build works alone.
The GUI executable must stay alive for a few seconds (it fails fast on missing modules).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def run(exe: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(exe), *args], capture_output=True, text=True, timeout=300, check=False
    )


def make_fixtures(folder: Path) -> tuple[Path, Path]:
    import py7zr

    zip_path = folder / "smoke.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("zip/hello.txt", "zip-ok")
    seven_path = folder / "smoke.7z"
    with py7zr.SevenZipFile(seven_path, "w") as archive:
        archive.writestr("7z-ok", "seven/hello.txt")
    return zip_path, seven_path


def check_cli(cli: Path, folder: Path) -> None:
    zip_path, seven_path = make_fixtures(folder)
    version = run(cli, "--version")
    if version.returncode != 0 or "RiftUnpack" not in version.stdout:
        fail(f"--version returned {version.returncode}: {version.stdout}{version.stderr}")

    out_zip = folder / "out-zip"
    result = run(cli, "extract", str(zip_path), "-o", str(out_zip))
    if result.returncode != 0:
        fail(f"extract zip returned {result.returncode}: {result.stdout}{result.stderr}")
    if (out_zip / "zip" / "hello.txt").read_text(encoding="utf-8") != "zip-ok":
        fail("ZIP content is wrong")

    out_7z = folder / "out-7z"
    result = run(cli, "extract", str(seven_path), "-o", str(out_7z))
    if result.returncode != 0:
        fail(f"extract 7z returned {result.returncode}: {result.stdout}{result.stderr}")
    if (out_7z / "seven" / "hello.txt").read_text(encoding="utf-8") != "7z-ok":
        fail("7z content is wrong")

    listed = run(cli, "list", str(seven_path))
    if listed.returncode != 0 or "seven/hello.txt" not in listed.stdout:
        fail(f"list 7z returned {listed.returncode}: {listed.stdout}{listed.stderr}")

    tested = run(cli, "test", str(zip_path))
    if tested.returncode != 0:
        fail(f"test zip returned {tested.returncode}: {tested.stdout}{tested.stderr}")
    print("CLI smoke test passed (version, extract ZIP/7z, list, test)")


def check_gui(gui: Path) -> None:
    process = subprocess.Popen([str(gui)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(8)
        if process.poll() is not None:
            fail(f"GUI executable exited early with code {process.returncode}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    print("GUI smoke test passed (window process stayed alive for 8 seconds)")


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        fail("usage: smoke_test_exe.py RiftUnpack.exe RiftUnpack-GUI.exe")
    cli, gui = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    for exe in (cli, gui):
        if not exe.is_file():
            fail(f"missing executable: {exe}")
    with tempfile.TemporaryDirectory() as tmp:
        check_cli(cli, Path(tmp))
    check_gui(gui)
    print("All executable smoke tests passed.")


if __name__ == "__main__":
    main(sys.argv)
