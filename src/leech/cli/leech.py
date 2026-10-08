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
from typing import Final, NoReturn, override

from leech import errors, parse, program, toolchain
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
    help = "build a program into an executable"
    description = (
        "Build a program and every module it imports into an executable. Intermediate "
        f"files go to {toolchain.OUT_DIR_NAME}/ beside ROOT."
    )
    option_groups = (RootArgument(), common.OptimizationOptions())

    @override
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "-o",
            help=f"executable to write (default: {toolchain.OUT_DIR_NAME}/<ROOT stem> beside ROOT)",
            metavar="EXE",
            type=pathlib.Path,
        )

    @override
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        common.build_exe(args.root, args.o, session)
        return 0


class RunCommand(common.Command):
    """Builds a program, then replaces this process with it."""

    name = "run"
    help = "build a program, then run it"
    description = (
        f"Build a program as 'leech build' does, into {toolchain.OUT_DIR_NAME}/ beside ROOT, "
        "then run it with any arguments given after '--'. The program's output and exit "
        "status are its own."
    )
    option_groups = (RootArgument(), common.OptimizationOptions())

    @override
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        exe = common.build_exe(args.root, None, session)
        self.render_diags(session)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            _exec_natively(exe, args.program_args)
        except OSError as err:
            raise errors.RunFailedError(exe, str(err)) from err


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
