import zipfile
from pathlib import Path

import archive_factory as make

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


def test_cli_escapes_markup_in_archive_names(tmp_path, capsys):
    # A file name that looks like Rich markup must be printed literally.
    path = tmp_path / "[bold]evil[bold].zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[red]x[red].txt", "data")
    assert main(["extract", str(path)]) == 0
    out = capsys.readouterr().out
    assert "[bold]evil[bold]" in out
    assert (tmp_path / "[bold]evil[bold]_extracted" / "[red]x[red].txt").exists()


def test_cli_batch_continues_after_unexpected_error(tmp_path, monkeypatch, capsys):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("a.txt", "a")
    boom = tmp_path / "boom.zip"
    with zipfile.ZipFile(boom, "w") as z:
        z.writestr("b.txt", "b")

    import riftunpack.cli as cli

    real = cli.extract_archive

    def flaky(archive, *args, **kwargs):
        if archive.name == "boom.zip":
            raise KeyError("internal failure")
        return real(archive, *args, **kwargs)

    monkeypatch.setattr(cli, "extract_archive", flaky)
    assert main(["extract", str(good), str(boom)]) == 1
    out = capsys.readouterr().out
    assert "unexpected error" in out
    assert "KeyError" in out
    assert "1/2 archives extracted" in out
    assert (tmp_path / "good_extracted" / "a.txt").exists()


def test_cli_multiple_archives_use_output_subdirectories(tmp_path):
    first = tmp_path / "one.zip"
    second = tmp_path / "two.zip"
    for path, name in ((first, "1.txt"), (second, "2.txt")):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(name, "x")
    out_dir = tmp_path / "out"
    assert main(["extract", str(first), str(second), "-o", str(out_dir)]) == 0
    assert (out_dir / "one" / "1.txt").exists()
    assert (out_dir / "two" / "2.txt").exists()


def test_cli_single_archive_output_is_direct(tmp_path):
    path = tmp_path / "solo.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("f.txt", "x")
    out_dir = tmp_path / "direct"
    assert main(["extract", str(path), "-o", str(out_dir)]) == 0
    assert (out_dir / "f.txt").exists()


def test_cli_no_archives_found(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["extract", str(empty)]) == 2
    assert "No recognized archives" in capsys.readouterr().err


def test_archive_base_name_strips_compound_and_split_suffixes():

    from riftunpack.detect import archive_base_name

    assert archive_base_name(Path("mods-b.tar.gz")) == "mods-b"
    assert archive_base_name(Path("MODS.TAR.XZ")) == "MODS"
    assert archive_base_name(Path("pack.tgz")) == "pack"
    assert archive_base_name(Path("data.7z.001")) == "data"
    assert archive_base_name(Path("pack.part2.rar")) == "pack"
    assert archive_base_name(Path("plain.zip")) == "plain"
    assert archive_base_name(Path("tar.gz")) == "tar"


def test_cli_tar_gz_gets_clean_output_folder(tmp_path):
    import io
    import tarfile

    path = tmp_path / "mods-b.tar.gz"
    data = b"tar-content"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("y/mod.ini")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    assert main(["extract", str(path)]) == 0
    assert (tmp_path / "mods-b_extracted" / "y" / "mod.ini").exists()
    assert not (tmp_path / "mods-b.tar_extracted").exists()


def test_cli_password_flag_prompts_and_extracts(tmp_path, monkeypatch):
    from riftunpack import cli

    path = tmp_path / "secret.7z"
    make.write_7z(path, {"s.txt": b"classified"}, password="hunter2")
    prompts = []
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda prompt="": prompts.append(prompt) or "hunter2"
    )
    assert main(["extract", str(path), "--password"]) == 0
    assert prompts, "password prompt was not shown"
    assert (tmp_path / "secret_extracted" / "s.txt").read_bytes() == b"classified"


def test_cli_password_is_prompted_only_when_needed(tmp_path, monkeypatch):
    from riftunpack import cli

    path = tmp_path / "locked.zip"
    calls = []
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": calls.append(prompt) or "")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("open.txt", "plain")
    assert main(["extract", str(path)]) == 0
    assert calls == []


def test_cli_output_survives_cp1252_pipe(tmp_path, monkeypatch):
    """Frozen Windows builds piped through cp1252 must not crash on ✓ or → in messages."""
    import io
    import sys

    path = tmp_path / "pipe.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.txt", "a")
    legacy = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", legacy)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    assert main(["extract", str(path)]) == 0
    legacy.flush()


def test_cli_seven_zip_option_is_passed_to_options():
    from riftunpack.cli import _options, _parser

    args = _parser().parse_args(["extract", "a.zip", "--seven-zip", r"C:\Tools\7z.exe"])
    assert _options(args).seven_zip == r"C:\Tools\7z.exe"
    plain = _parser().parse_args(["extract", "a.zip"])
    assert _options(plain).seven_zip is None


def test_cli_test_command_reports_ok(tmp_path, capsys):
    path = tmp_path / "ok.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("file.txt", "content")
    assert main(["test", str(path)]) == 0
    assert "OK:" in capsys.readouterr().out


def test_cli_extract_here_uses_archive_parent(tmp_path):
    path = tmp_path / "here.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("note.txt", "nearby")
    assert main(["extract", "--here", str(path)]) == 0
    assert (tmp_path / "note.txt").exists()


def test_cli_list_encrypted_archive_reprompts_for_password(tmp_path, monkeypatch):
    from riftunpack import cli

    path = tmp_path / "vault.7z"
    make.write_7z(path, {"hidden.txt": b"data"}, password="s3cret")
    prompts = []
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda prompt="": prompts.append(prompt) or "s3cret"
    )
    assert main(["list", str(path)]) == 0
    assert prompts, "encrypted listing without --password must prompt once"


def test_cli_extract_encrypted_archive_reprompts_for_password(tmp_path, monkeypatch):
    from riftunpack import cli

    path = tmp_path / "locked.7z"
    make.write_7z(path, {"payload.txt": b"data"}, password="s3cret")
    prompts = []
    monkeypatch.setattr(
        cli.getpass, "getpass", lambda prompt="": prompts.append(prompt) or "s3cret"
    )
    assert main(["extract", str(path)]) == 0
    assert prompts, "encrypted extract without --password must prompt"
    assert (tmp_path / "locked_extracted" / "payload.txt").exists()


def test_cli_batch_known_error_is_reported(tmp_path, monkeypatch, capsys):
    import riftunpack.cli as cli
    from riftunpack.errors import RiftUnpackError

    path = tmp_path / "broken.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.txt", "a")

    def refuse(archive, *args, **kwargs):
        raise RiftUnpackError("unsupported archive feature")

    monkeypatch.setattr(cli, "extract_archive", refuse)
    assert main(["extract", str(path)]) == 1
    out = capsys.readouterr().out
    assert "unsupported archive feature" in out
    assert "0/1 archives extracted" in out


def test_cli_list_missing_file_is_friendly(tmp_path, capsys):
    missing = tmp_path / "nope.zip"
    assert main(["list", str(missing)]) == 1
    assert "RiftUnpack:" in capsys.readouterr().err
