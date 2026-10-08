# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""What the command-line tools share: commands, option groups, crash reporting and builds."""

import abc
import argparse
import contextlib
import importlib.metadata
import pathlib
from collections.abc import Iterator
from typing import ClassVar, Optional

import llvmlite
from llvmlite import binding as llb

from leech import diag, errors, ll_emit, opt_util, program, target, toolchain
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

    An error already reported, raised as ``diag.ReportedError``, is suppressed too.
    """
    try:
        yield
    except errors.UserError as err:
        session.diags.error(err)
    except diag.ReportedError:
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


class Command(abc.ABC):
    """A command-line command: its arguments, and what it does in a session.

    The command's diagnostics are rendered once it finishes, unless it rendered them itself
    with ``render_diags``. A user error it raises is
    reported to the session and fails the command, as an error already reported does; any
    other exception is reported as an internal compiler error.
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

    def failure_status(self, session: session_mod.Session) -> int:
        """The exit status when ``run`` raises a user error."""
        return 1

    def execute(self, args: argparse.Namespace, tool: str) -> int:
        """Run the command in a new session, render its diagnostics, and return the exit
        status."""
        session = session_mod.Session()
        for group in self.option_groups:
            group.configure(args, session)
        with self._reporting_crashes(session, tool):
            status: Optional[int] = None
            with reporting_user_errors(session):
                status = self.run(args, session)
            if status is None:
                status = self.failure_status(session)
        self.render_diags(session)
        return status

    def render_diags(self, session: session_mod.Session) -> None:
        """Render the session's diagnostics that haven't been rendered yet, in source order."""
        errors.TextErrorRenderer().display_errors(self._take_unrendered(session))

    def _take_unrendered(self, session: session_mod.Session) -> list[errors.UserError]:
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


def build_exe(
    root: pathlib.Path, output: Optional[pathlib.Path], session: session_mod.Session
) -> pathlib.Path:
    """Build the program whose root module is ``root`` into an executable at ``output``.

    Intermediate files go to the output directory beside the root, and the executable to
    ``output`` or its default path there, replaced only once linking succeeds. Returns the
    executable's absolute path.
    """
    linker = toolchain.Linker.from_env()
    checked = program.Program(root, entry=True).check(session)
    out_dir = toolchain.OutputDir(root)
    exe = opt_util.opt_or_default(output, out_dir.default_exe).absolute()
    try:
        out_dir.prepare()
        obj = out_dir.emit_object(checked.module_llvm_irs(), session.opt_level)
        linker.link(obj, exe)
    except OSError as err:
        raise errors.BuildOutputError(str(err)) from err
    return exe
