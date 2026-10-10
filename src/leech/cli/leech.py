# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The ``leech`` command: build and run Leech programs from their root modules."""

import argparse
import hashlib
import os
import pathlib
import signal
import sys
from collections.abc import Mapping, Sequence
from typing import Final, NoReturn, override

from leech import diag, diag_kinds, parse, program, toolchain
from leech import session as session_mod
from leech.cli import common, doctor


class RootArgument(common.OptionGroup):
    """``ROOT``, an existing ``.leech`` file named by one identifier."""

    @override
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "root",
            help="the program's root module, whose directory is the program's package",
            metavar="ROOT",
            type=pathlib.Path,
        )

    @override
    def validate(self, parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        root: pathlib.Path = args.root
        if not root.is_file():
            parser.error(f"root module {str(root)!r} is not a file")
        if root.suffix != ".leech":
            parser.error(f"root module file name must end in '.leech': {str(root)!r}")
        segments = parse.parse_qualified_name(root.stem)
        if segments is None or len(segments) != 1:
            parser.error(
                f"root module file name must be an identifier followed by '.leech': {str(root)!r}"
            )


class BuildCommand(common.Command):
    name = "build"
    help = "build a program into an executable or other outputs"
    description = (
        "Build a program and every module it imports. Each output is named after ROOT's "
        "stem and written to the current directory, unless -o names it. A program that "
        "fails to compile or link writes nothing."
    )
    option_groups = (RootArgument(), common.OutputOptions(), common.OptimizationOptions())

    @override
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        common.build(args.root, args.outputs, session)
        return 0


class RunCommand(common.Command):
    """Builds a program into a per-user cache, then replaces this process with it."""

    name = "run"
    help = "build a program, then run it"
    description = (
        "Build a program as 'leech build' does, into a per-user cache rather than the current "
        "directory, then run it with any arguments given after '--'. The program's output and "
        "exit status are its own."
    )
    option_groups = (RootArgument(), common.OptimizationOptions())

    @override
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        exe = _run_cache_dir(args.root, session.diags) / args.root.stem
        common.build(args.root, {toolchain.OutputKind.EXE: exe}, session)
        self.render_diags(session)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            _exec_natively(exe, args.program_args)
        except OSError as err:
            session.diags.raise_error(diag_kinds.RUN_FAILURE, None, exe=str(exe), reason=str(err))


def _run_cache_dir(root: pathlib.Path, diags: diag.Diags) -> pathlib.Path:
    """Return the absolute directory ``leech run`` builds ``root``'s executable in, creating it.

    It is ``leech/run/<sha256 of the root's absolute path>`` in the user's cache directory.
    Directories created are private to the user.
    """
    name = hashlib.sha256(os.fsencode(root.absolute())).hexdigest()
    directory = _cache_home() / "leech" / "run" / name
    try:
        for path in reversed((directory, *directory.parents)):
            if not path.is_dir():
                path.mkdir(mode=0o700, exist_ok=True)
    except OSError as err:
        diags.raise_error(diag_kinds.UNWRITABLE_OUTPUT, None, reason=str(err))
    return directory


def _cache_home() -> pathlib.Path:
    """Return ``$XDG_CACHE_HOME`` if it is absolute, as the XDG specification requires, or
    ``~/.cache``."""
    cache = pathlib.Path(os.environ.get("XDG_CACHE_HOME", ""))
    if cache.is_absolute():
        return cache
    return pathlib.Path.home() / ".cache"


class CheckCommand(common.Command):
    name = "check"
    help = "report a program's diagnostics without building it"
    description = (
        "Compile a program as 'leech build' does and report its diagnostics, without "
        "writing any files."
    )
    option_groups = (RootArgument(),)

    @override
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        program.Program(args.root, entry=True).check(session)
        return 0


COMMANDS: Final[tuple[type[common.Command], ...]] = (
    BuildCommand,
    RunCommand,
    CheckCommand,
    doctor.DoctorCommand,
)
"""Every ``leech`` command, in the order ``--help`` lists them."""


def make_parser(
    commands: Sequence[common.Command],
) -> tuple[argparse.ArgumentParser, Mapping[str, argparse.ArgumentParser]]:
    """Return the ``leech`` parser and its commands' parsers, by command name."""
    parser = argparse.ArgumentParser(
        prog="leech", description="Build and run Leech programs.", allow_abbrev=False
    )
    parser.add_argument("--version", action="version", version=common.version_text("leech"))
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    command_parsers = dict[str, argparse.ArgumentParser]()
    for command in commands:
        command_parser = subparsers.add_parser(
            command.name, help=command.help, description=command.description, allow_abbrev=False
        )
        command.add_arguments(command_parser)
        command_parsers[command.name] = command_parser
    return parser, command_parsers


def _parse_args(
    argv: Sequence[str], commands: Sequence[common.Command]
) -> tuple[common.Command, argparse.Namespace]:
    """Parse ``leech`` arguments, returning the command to run and its arguments.

    The arguments are split at the first ``--`` before argparse sees them, so everything after
    it reaches the program untouched, as ``program_args``, while options may still follow
    ``ROOT``.
    """
    program_args: list[str] = []
    has_separator = "--" in argv
    if has_separator:
        index = argv.index("--")
        argv, program_args = argv[:index], list(argv[index + 1 :])
    parser, command_parsers = make_parser(commands)
    args = parser.parse_args(argv)
    command = next(command for command in commands if command.name == args.command)
    command_parser = command_parsers[command.name]
    if has_separator and not isinstance(command, RunCommand):
        command_parser.error("arguments after '--' are only accepted by 'leech run'")
    args.program_args = program_args
    command.validate(command_parser, args)
    return command, args


def main() -> NoReturn:
    """Check, build, or build and run the requested program, or diagnose the toolchain.

    ``leech check`` and ``leech build`` exit 0 only on success. ``leech run`` replaces this
    process with the program.
    """
    command, args = _parse_args(sys.argv[1:], [command_type() for command_type in COMMANDS])
    sys.exit(command.execute(args, "leech"))


_PYTHON_IGNORED_SIGNALS = (signal.SIGPIPE, signal.SIGXFSZ)
"""Signals the Python runtime ignores at start-up, which an executed program would inherit."""


def _exec_natively(exe: pathlib.Path, args: Sequence[str]) -> NoReturn:
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
