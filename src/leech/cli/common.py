# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""What the ``leech`` commands share: the command and option-group bases, and builds."""

import abc
import argparse
import contextlib
import importlib.metadata
import pathlib
from collections.abc import Iterator, Mapping
from typing import ClassVar

import llvmlite
from llvmlite import binding as llb

from leech import diag, errors, ll_emit, program, target, toolchain
from leech import session as session_mod


def version_text(prog: str) -> str:
    """Describe the installed compiler version and its LLVM backend, prefixed by ``prog``."""
    llvm_version = ".".join(str(part) for part in llb.llvm_version_info)
    return (
        f"{prog} {importlib.metadata.version('leech')} "
        f"(LLVM {llvm_version} via llvmlite {llvmlite.__version__}; target {target.TRIPLE})"
    )


@contextlib.contextmanager
def reporting_user_errors(session: session_mod.Session) -> Iterator[None]:
    """Report a user error escaping the block to the session, and suppress it.

    Errors already reported, raised as ``diag.ReportedError`` or ``diag.CompilationError``,
    are suppressed too.
    """
    try:
        yield
    except errors.UserError as err:
        session.diags.error(err)
    except diag.ReportedError, diag.CompilationError:
        pass


class OptionGroup(abc.ABC):
    """Options several commands share, and how they configure a session."""

    @abc.abstractmethod
    def add_arguments(self, parser: argparse.ArgumentParser) -> None: ...

    def validate(  # noqa: B027 - intentionally empty default, not abstract
        self, parser: argparse.ArgumentParser, args: argparse.Namespace
    ) -> None:
        """Reject invalid arguments with ``parser.error``."""

    def configure(  # noqa: B027 - intentionally empty default, not abstract
        self, args: argparse.Namespace, session: session_mod.Session
    ) -> None:
        pass


class OptimizationOptions(OptionGroup):
    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "-O",
            choices=ll_emit.OPT_LEVELS,
            default=0,
            type=int,
            help="optimization level (default: %(default)s)",
            dest="opt_level",
        )

    def configure(self, args: argparse.Namespace, session: session_mod.Session) -> None:
        session.opt_level = args.opt_level


def _parse_output_kinds(value: str) -> frozenset[toolchain.OutputKind]:
    kinds = set[toolchain.OutputKind]()
    for name in value.split(","):
        try:
            kinds.add(toolchain.OutputKind(name))
        except ValueError:
            choices = ", ".join(kind.value for kind in toolchain.OutputKind)
            raise argparse.ArgumentTypeError(
                f"invalid kind {name!r} (choose from {choices})"
            ) from None
    return frozenset(kinds)


class OutputOptions(OptionGroup):
    """``--emit`` and ``-o``: which outputs to write, and where.

    Validation sets ``args.outputs``, mapping each requested kind to its path: ``-o``, or the
    root's stem with the kind's suffix in the current directory.
    """

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        kinds = ", ".join(kind.value for kind in toolchain.OutputKind)
        parser.add_argument(
            "--emit",
            default=frozenset({toolchain.OutputKind.EXE}),
            help=f"comma-separated outputs to write, from {kinds} (default: exe)",
            metavar="KIND[,KIND...]",
            type=_parse_output_kinds,
        )
        parser.add_argument(
            "-o",
            help="the output's path, with one --emit kind (default: ./<ROOT stem><suffix>)",
            metavar="PATH",
            type=pathlib.Path,
        )

    def validate(self, parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        kinds: frozenset[toolchain.OutputKind] = args.emit
        if args.o is not None:
            if len(kinds) != 1:
                parser.error("-o is only accepted with one --emit kind")
            (kind,) = kinds
            args.outputs = {kind: args.o}
        else:
            args.outputs = {kind: pathlib.Path(f"{args.root.stem}{kind.suffix}") for kind in kinds}


class Command(abc.ABC):
    """A command-line command: its arguments, and what it does in a session.

    The command's diagnostics are rendered once it finishes, unless it rendered them itself
    with ``render_diags``. A user error it raises is
    reported to the session and fails the command with status 1, as an error already reported
    does; any other exception is reported as an internal compiler error.
    """

    name: ClassVar[str]
    help: ClassVar[str]
    description: ClassVar[str]
    option_groups: ClassVar[tuple[OptionGroup, ...]] = ()

    #: The identities of the diagnostics rendered so far.
    _rendered: set[int]

    def __init__(self) -> None:
        self._rendered = set()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        for group in self.option_groups:
            group.add_arguments(parser)

    def validate(self, parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        """Reject invalid arguments with ``parser.error``."""
        for group in self.option_groups:
            group.validate(parser, args)

    @abc.abstractmethod
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        """Do the command's work, returning the exit status."""

    def execute(self, args: argparse.Namespace, tool: str) -> int:
        """Run the command in a new session, render its diagnostics, and return the exit
        status."""
        session = session_mod.Session()
        for group in self.option_groups:
            group.configure(args, session)
        with self._reporting_crashes(session, tool):
            status = 1
            with reporting_user_errors(session):
                status = self.run(args, session)
        self.render_diags(session)
        return status

    def render_diags(self, session: session_mod.Session) -> None:
        """Render the session's diagnostics that haven't been rendered yet, in source order."""
        errors.TextErrorRenderer().display_errors(self._take_unrendered(session))

    def _take_unrendered(self, session: session_mod.Session) -> list[diag.AnyDiag]:
        unrendered = [err for err in session.diags.sorted() if id(err) not in self._rendered]
        self._rendered.update(id(err) for err in unrendered)
        return unrendered

    @contextlib.contextmanager
    def _reporting_crashes(self, session: session_mod.Session, tool: str) -> Iterator[None]:
        """Report an exception escaping the block as an internal error in ``tool``.

        The diagnostics not rendered yet are rendered first, then the crash as a bug in
        ``tool``, and the exception propagates.
        """
        try:
            yield
        except Exception as err:
            errors.TextErrorRenderer().display_internal_error(
                self._take_unrendered(session), err, tool
            )
            raise


def build(
    root: pathlib.Path,
    outputs: Mapping[toolchain.OutputKind, pathlib.Path],
    session: session_mod.Session,
) -> None:
    """Build the program whose root module is ``root``, writing each requested output to its
    path, as ``toolchain.write_outputs`` does.

    A requested executable needs ``$CC``, which is resolved before the program is checked.
    """
    linker = None
    if toolchain.OutputKind.EXE in outputs:
        linker = toolchain.Linker.from_env()
    checked = program.Program(root, entry=True).check(session)
    toolchain.write_outputs(checked.llvm_module(), outputs, session.opt_level, linker)
