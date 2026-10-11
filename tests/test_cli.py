import zipfile

from riftunpack.cli import main


def test_cli_extract_and_list(tmp_path, capsys):
    path = tmp_path / "game.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mod.txt", "hello")
    assert main(["list", str(path)]) == 0
    assert "mod.txt" in capsys.readouterr().out
    assert main(["extract", str(path)]) == 0
    assert (tmp_path / "game_extracted/mod.txt").exists()


def test_cli_malformed_archive_is_friendly(tmp_path, capsys):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not an archive")
    assert main(["extract", str(bad)]) == 2
