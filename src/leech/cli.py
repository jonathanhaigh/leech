# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The ``leech`` command: build and run Leech programs from their root modules."""

import argparse
import os
import pathlib
import signal
import sys
from collections.abc import Mapping, Sequence

from leech import build, doctor, driver, errors, ll_emit, parse


def _make_parser() -> tuple[argparse.ArgumentParser, Mapping[str, argparse.ArgumentParser]]:
    """Return the ``leech`` parser and its subcommands' parsers, by command name."""
    parser = argparse.ArgumentParser(
        prog="leech", description="Build and run Leech programs.", allow_abbrev=False
    )
    parser.add_argument("--version", action="version", version=driver.version_text("leech"))
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    build_parser = subparsers.add_parser(
        "build",
        help="build a program into an executable",
        description=(
            "Build a program and every module it imports into an executable. Intermediate "
            f"files go to {build.OUT_DIR_NAME}/ beside ROOT."
        ),
        allow_abbrev=False,
    )
    run_parser = subparsers.add_parser(
        "run",
        help="build a program, then run it",
        description=(
            f"Build a program as 'leech build' does, into {build.OUT_DIR_NAME}/ beside ROOT, "
            "then run it with any arguments given after '--'. The program's output and exit "
            "status are its own."
        ),
        allow_abbrev=False,
    )

    check_parser = subparsers.add_parser(
        "check",
        help="report a program's diagnostics without building it",
        description=(
            "Compile a program as 'leech build' does and report its diagnostics, without "
            "writing any files."
        ),
        allow_abbrev=False,
    )

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check that the toolchain can build and run programs",
        description=(
            "Print the versions of leech and its toolchain, then build and run a test "
            "program, reporting any problem and how to fix it."
        ),
        allow_abbrev=False,
    )

    for subparser in (build_parser, run_parser, check_parser):
        subparser.add_argument(
            "root",
            help="the program's root module, whose directory is the program's package",
            metavar="ROOT",
            type=pathlib.Path,
        )
    for subparser in (build_parser, run_parser):
        subparser.add_argument(
            "-O",
            choices=ll_emit.OPT_LEVELS,
            default=0,
            type=int,
            help="optimization level (default: %(default)s)",
            dest="opt_level",
        )
    build_parser.add_argument(
        "-o",
        help=f"executable to write (default: {build.OUT_DIR_NAME}/<ROOT stem> beside ROOT)",
        metavar="EXE",
        type=pathlib.Path,
    )
    subparsers_by_name = {
        "build": build_parser,
        "run": run_parser,
        "check": check_parser,
        "doctor": doctor_parser,
    }
    return parser, subparsers_by_name


def _parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parse ``leech`` arguments, returning them and the arguments for the program to run.

    The arguments are split at the first ``--`` before argparse sees them, so everything after
    it reaches the program untouched while options may still follow ``ROOT``.
    """
    program_args: list[str] = []
    has_separator = "--" in argv
    if has_separator:
        index = argv.index("--")
        argv, program_args = argv[:index], list(argv[index + 1 :])
    parser, subparsers = _make_parser()
    args = parser.parse_args(argv)
    subparser = subparsers[args.command]
    if has_separator and args.command != "run":
        subparser.error("arguments after '--' are only accepted by 'leech run'")
    if args.command != "doctor":
        _check_root(subparser, args.root)
    return args, program_args


def _check_root(parser: argparse.ArgumentParser, root: pathlib.Path) -> None:
    """Reject a root that isn't an existing ``.leech`` file named by one identifier."""
    if not root.is_file():
        parser.error(f"root module {str(root)!r} is not a file")
    if root.suffix != ".leech":
        parser.error(f"root module file name must end in '.leech': {str(root)!r}")
    segments = parse.parse_qualified_name(root.stem)
    if segments is None or len(segments) != 1:
        parser.error(
            f"root module file name must be an identifier followed by '.leech': {str(root)!r}"
        )


def main() -> None:
    """Check, build, or build and run the requested program, or diagnose the toolchain.

    ``leech check`` and ``leech build`` exit 0 only on success. ``leech run`` replaces this
    process with the program.
    """
    args, program_args = _parse_args(sys.argv[1:])
    renderer = errors.TextErrorRenderer()
    if args.command == "doctor":
        sys.exit(0 if doctor.diagnose(sys.stdout) else 1)
    if args.command == "check":
        diags = build.check(args.root)
        renderer.display_errors(list(diags))
        if any(diag.level >= errors.ERROR for diag in diags):
            sys.exit(1)
        sys.exit(0)
    result = build.build(args.root, output=getattr(args, "o", None), opt_level=args.opt_level)
    renderer.display_errors(list(result.diags))
    if result.exe is None:
        sys.exit(1)
    if args.command == "run":
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            _exec_natively(result.exe, program_args)
        except OSError as err:
            renderer.display_errors([errors.RunFailedError(result.exe, str(err))])
            sys.exit(1)
    sys.exit(0)


_PYTHON_IGNORED_SIGNALS = (signal.SIGPIPE, signal.SIGXFSZ)
"""Signals the Python runtime ignores at start-up, which an executed program would inherit."""


def _exec_natively(exe: pathlib.Path, args: Sequence[str]) -> None:
    """Replace this process with ``exe``, with the signal handling it gets when run directly.

    Raises ``OSError`` without changing anything if ``exe`` can't be executed.
    """
    previous = {sig: signal.signal(sig, signal.SIG_DFL) for sig in _PYTHON_IGNORED_SIGNALS}
    try:
        os.execv(str(exe), [str(exe), *args])
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)
