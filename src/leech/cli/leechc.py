# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The ``leechc`` command: compile one Leech module."""

import argparse
import pathlib
import sys
from typing import NoReturn, override

from leech import errors, ll_emit, parse, program, reserved
from leech import session as session_mod
from leech.cli import common


def _parse_module_name(value: str) -> str:
    segments = parse.parse_qualified_name(value)
    if segments is None or reserved.is_reserved(segments[-1]):
        raise argparse.ArgumentTypeError(f"invalid qualified module name: {value!r}")
    return value


class LeechcCommand(common.Command):
    """Compiles one module, as checked in its program, exiting with its diagnostics' level."""

    name = "leechc"
    help = "compile one Leech module"
    description = "Compile one Leech module."
    option_groups = (common.OptimizationOptions(),)

    @override
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("filename", help="source file", type=pathlib.Path)
        parser.add_argument("-o", help="output file", metavar="FILENAME", type=pathlib.Path)
        parser.add_argument(
            "--module-name",
            help=(
                "the module's qualified name, matching its location: x::a for x/a.leech in the "
                "package directory (defaults to the source file stem)"
            ),
            metavar="NAME",
            type=_parse_module_name,
        )
        parser.add_argument(
            "--entry",
            action="store_true",
            help="make this module's main function the program entry point",
        )
        parser.add_argument(
            "--emit",
            choices=[kind.value for kind in ll_emit.EmitKind],
            default=ll_emit.EmitKind.LLVM_IR.value,
            help="output format (default: %(default)s)",
        )
        super().add_arguments(parser)

    @override
    def validate(self, parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        """Default the output to the input path with the emitted format's suffix in place of
        ``.leech``."""
        super().validate(parser, args)
        args.emit = ll_emit.EmitKind(args.emit)
        if args.filename.suffix != ".leech":
            parser.error(f"source file name must end in '.leech': {str(args.filename)!r}")
        if args.o is None:
            args.o = args.filename.with_suffix(args.emit.suffix)

    @override
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        checked = program.check_module(
            args.filename, session, qualified_name=args.module_name, entry=args.entry
        )
        output = ll_emit.emit_from_ir(checked.llvm_ir(), args.emit, session.opt_level)
        try:
            pathlib.Path(args.o).write_bytes(output)
        except OSError as err:
            raise errors.BuildOutputError(str(err)) from err
        return session.diags.level

    @override
    def failure_status(self, session: session_mod.Session) -> int:
        return session.diags.level


def main() -> NoReturn:
    """Compile CLI input, render diagnostics, and exit with their severity."""
    command = LeechcCommand()
    parser = argparse.ArgumentParser(
        prog=command.name, description=command.description, allow_abbrev=False
    )
    parser.add_argument("--version", action="version", version=common.version_text(command.name))
    command.add_arguments(parser)
    args = parser.parse_args()
    command.validate(parser, args)
    sys.exit(command.execute(args, command.name))
