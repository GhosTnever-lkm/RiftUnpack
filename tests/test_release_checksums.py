import hashlib
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "create_checksums.py"


def test_manifest_hashes_files_in_stable_name_order(tmp_path):
    gui = tmp_path / "RiftUnpack-GUI.exe"
    cli = tmp_path / "RiftUnpack.exe"
    output = tmp_path / "release" / "SHA256SUMS.txt"
    gui.write_bytes(b"gui executable fixture")
    cli.write_bytes(b"cli executable fixture")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), str(gui), str(cli)],
        capture_output=True,
        text=True,
        check=False,
    )

    expected = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in (gui, cli)
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="ascii") == expected


def test_manifest_rejects_missing_release_files(tmp_path):
    output = tmp_path / "SHA256SUMS.txt"
    missing = tmp_path / "RiftUnpack.exe"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output), str(missing)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "release file not found" in result.stderr
    assert not output.exists()
