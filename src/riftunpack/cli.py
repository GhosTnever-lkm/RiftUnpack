"""Command-line interface for RiftUnpack."""

from __future__ import annotations

import argparse
import getpass
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from . import __version__
from .detect import detect_format
from .engine import ExtractOptions, expand_inputs, extract_archive, list_archive, verify_archive
from .errors import PasswordRequiredError, RiftUnpackError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="riftunpack", description="Fast, safe, offline archive extraction"
    )
    parser.add_argument("--version", action="version", version=f"RiftUnpack {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract", help="extract one or more archives")
    extract.add_argument("inputs", nargs="+", help="archive file(s) or folder(s)")
    extract.add_argument(
        "-o", "--output", help="destination folder (default: each archive gets a sibling folder)"
    )
    extract.add_argument(
        "--here",
        action="store_true",
        help="extract alongside the source archive (Windows context menu)",
    )
    extract.add_argument(
        "-r", "--recursive", action="store_true", help="find archives in input folders recursively"
    )
    extract.add_argument(
        "--include", action="append", help="member file mask (repeatable, e.g. '*.pak')"
    )
    extract.add_argument("--conflict", choices=("overwrite", "skip", "rename"), default="rename")
    extract.add_argument(
        "--password", action="store_true", help="prompt securely for an archive password"
    )
    extract.add_argument(
        "--encoding", help="archive filename encoding override (e.g. cp866, cp1251, shift_jis)"
    )
    extract.add_argument(
        "--deep", action="store_true", help="also extract archives inside archives"
    )
    extract.add_argument(
        "--max-depth", type=int, default=2, help="maximum nested archive depth (default: 2)"
    )
    extract.add_argument(
        "--max-size-gb",
        type=float,
        default=20,
        help="maximum expanded size per archive (default: 20 GB)",
    )
    extract.add_argument("--max-files", type=int, default=100000, help="maximum files per archive")
    extract.add_argument("--max-ratio", type=float, default=1000, help="maximum compression ratio")
    extract.add_argument(
        "-j", "--jobs", type=int, default=1, help="parallel archive workers (default: 1)"
    )
    listing = sub.add_parser("list", help="list archive contents without extracting")
    listing.add_argument("archive")
    listing.add_argument("--password", action="store_true")
    listing.add_argument("--encoding")
    test = sub.add_parser(
        "test", help="check archive structure and CRC by extracting to a temporary folder"
    )
    test.add_argument("archive")
    test.add_argument("--password", action="store_true")
    test.add_argument("--max-size-gb", type=float, default=20)
    test.add_argument("--max-files", type=int, default=100000)
    return parser


def _options(args, password: str | None = None) -> ExtractOptions:
    return ExtractOptions(
        conflict=getattr(args, "conflict", "rename"),
        max_total_size=int(getattr(args, "max_size_gb", 20) * 1024**3),
        max_files=getattr(args, "max_files", 100000),
        max_ratio=getattr(args, "max_ratio", 1000),
        password=password,
        include=getattr(args, "include", None),
        encoding=getattr(args, "encoding", None),
    )


def _password(prompt: bool) -> str | None:
    return getpass.getpass("Archive password (leave blank if none): ") if prompt else None


def _password_retry(prompt_flag: bool, operation):
    """Retry once with a secure prompt when an encrypted archive requests a password."""
    password = _password(prompt_flag)
    try:
        return operation(password)
    except PasswordRequiredError:
        if prompt_flag:
            raise
        return operation(_password(True))


def _destination(archive: Path, args) -> Path:
    if args.here:
        return archive.parent
    if args.output:
        return (
            Path(args.output)
            if len(getattr(args, "_archives", [])) <= 1
            else Path(args.output) / archive.stem
        )
    return archive.with_name(f"{archive.stem}_extracted")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "list":
            members = _password_retry(
                args.password, lambda password: list_archive(args.archive, _options(args, password))
            )
            print(
                f"{Path(args.archive).name} — {detect_format(args.archive)} — "
                f"{len(members)} entries"
            )
            for item in members:
                size = "<DIR>" if item.is_dir else f"{item.size:,} B"
                print(f"{size:>14}  {item.name}")
            return 0
        if args.command == "test":
            _password_retry(
                args.password,
                lambda password: verify_archive(args.archive, _options(args, password)),
            )
            print(f"OK: {args.archive}")
            return 0

        archives = expand_inputs(args.inputs, recursive=args.recursive or args.here)
        if not archives:
            print("No recognized archives found.", file=sys.stderr)
            return 2
        args._archives = archives
        console = Console()
        console.print(f"[bold cyan]RiftUnpack[/bold cyan] — {len(archives)} archive(s)")
        results = []
        password = _password(args.password)
        prompt_lock = threading.Lock()

        progress_ui = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            transient=True,
        )
        with progress_ui:
            task_ids = {
                archive: progress_ui.add_task(archive.name, total=1) for archive in archives
            }

            def run(archive: Path):
                task_id = task_ids[archive]
                destination = _destination(archive, args)

                def progress(current, total, name):
                    progress_ui.update(
                        task_id,
                        description=f"{archive.name}: {name[:48]}",
                        total=max(total, 1),
                        completed=current,
                    )

                def extract(pwd):
                    return extract_archive(
                        archive,
                        destination,
                        _options(args, pwd),
                        progress=progress,
                        deep=args.deep,
                        max_depth=max(0, args.max_depth),
                    )

                try:
                    return extract(password)
                except PasswordRequiredError:
                    if args.password:
                        raise
                    with prompt_lock:
                        return extract(_password(True))

            with ThreadPoolExecutor(max_workers=max(1, min(args.jobs, len(archives)))) as pool:
                futures = {pool.submit(run, archive): archive for archive in archives}
                for future in as_completed(futures):
                    archive = futures[future]
                    try:
                        report = future.result()
                        results.append(report)
                        console.print(
                            f"[green]✓[/green] {archive.name}: {report.extracted} files, "
                            f"{report.bytes_written:,} bytes → {report.destination}"
                        )
                        for warning in report.warnings:
                            console.print(f"  [yellow]! {warning}[/yellow]")
                    except RiftUnpackError as exc:
                        results.append(None)
                        progress_ui.update(
                            task_ids[archive], description=f"[red]{archive.name}: failed[/red]"
                        )
                        console.print(f"[red]✗ {archive.name}: {exc}[/red]")
        good = sum(item is not None for item in results)
        console.print(f"Summary: {good}/{len(archives)} archives extracted.")
        return 0 if good == len(archives) else 1
    except (RiftUnpackError, OSError, ValueError) as exc:
        print(f"RiftUnpack: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
