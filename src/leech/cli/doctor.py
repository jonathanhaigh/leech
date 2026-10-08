# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The ``leech doctor`` command: check that the toolchain can build and run Leech programs."""

import argparse
import pathlib
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Final, Optional, TextIO, override

from leech import errors, toolchain
from leech import session as session_mod
from leech.cli import common

_TEST_OUTPUT: Final[str] = "leech doctor\n"
_TEST_PROGRAM: Final[str] = (
    "import std::io;\n"
    "pub fn main() i32 {\n"
    f'    io::println("{_TEST_OUTPUT.rstrip()}");\n'
    "    return 0;\n"
    "}\n"
)
_CC_HELP: Final[str] = "Check that CC names a C compiler that can link programs, such as gcc"


_TIMEOUT_SECONDS: Final[int] = 60
"""How long each external process may run before the check fails."""


class DoctorCommand(common.Command):
    name = "doctor"
    help = "check that the toolchain can build and run programs"
    description = (
        "Print the versions of leech and its toolchain, then build and run a test "
        "program, reporting any problem and how to fix it."
    )

    @override
    def run(self, args: argparse.Namespace, session: session_mod.Session) -> int:
        if self._diagnose(sys.stdout, session):
            return 0
        return 1

    def _diagnose(self, report: TextIO, session: session_mod.Session) -> bool:
        """Write versions and toolchain checks to ``report``; return whether every check passed.

        Problems are reported to ``session`` and rendered beneath the check that found them,
        with a note on how to fix those caused by the C compiler.
        """
        report.write(f"{common.version_text('leech')}\n")
        report.write(f"Python {platform.python_version()}\n")
        try:
            cc = list(toolchain.Linker.from_env().command)
        except errors.UserError as err:
            return self._fail(report, "C compiler", session, err)
        report.write(f"C compiler: {shlex.join(cc)} ({shutil.which(cc[0])})\n")
        try:
            version = _cc_version(cc)
        except errors.DoctorCheckError as err:
            return self._fail(report, "C compiler version", session, err)
        report.write(f"  {version}\n")
        with tempfile.TemporaryDirectory(prefix="leech-doctor-") as tmp_dir:
            root = pathlib.Path(tmp_dir) / "doctor.leech"
            root.write_text(_TEST_PROGRAM)
            exe = pathlib.Path(tmp_dir) / "doctor"
            built = False
            with common.reporting_user_errors(session):
                common.build(root, {toolchain.OutputKind.EXE: exe}, session)
                built = True
            if not built:
                linking = any(
                    isinstance(err, errors.LinkFailedError) for err in session.diags.all()
                )
                return self._fail(report, "Build a test program", session, fix=linking)
            try:
                proc = _run([str(exe)], "the test program")
            except errors.DoctorCheckError as err:
                return self._fail(report, "Run the test program", session, err)
            if proc.returncode != 0 or proc.stdout != _TEST_OUTPUT:
                problem = errors.DoctorCheckError(
                    f"The test program exited with status {proc.returncode} and printed "
                    f"{proc.stdout!r}, expecting status 0 and {_TEST_OUTPUT!r}",
                    _CC_HELP,
                )
                return self._fail(report, "Run the test program", session, problem)
        report.write("Build and run a test program: ok\n")
        return True

    def _fail(
        self,
        report: TextIO,
        check: str,
        session: session_mod.Session,
        err: Optional[errors.UserError] = None,
        *,
        fix: bool = False,
    ) -> bool:
        """Report ``check`` as failed, followed by ``err`` and the session's other diagnostics.

        With ``fix``, a note on fixing the C compiler follows them.
        """
        if err is not None:
            session.diags.error(err)
        report.write(f"{check}: FAILED\n")
        report.flush()
        self.render_diags(session)
        if fix:
            errors.TextErrorRenderer().display_errors([errors.DoctorFixNote(_CC_HELP)])
        return False


def _cc_version(cc: list[str]) -> str:
    """Return the first line ``cc --version`` prints."""
    command = shlex.join([*cc, "--version"])
    proc = _run([*cc, "--version"], f"`{command}`")
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or not lines:
        raise errors.DoctorCheckError(
            f"`{command}` exited with status {proc.returncode} and printed no version", _CC_HELP
        )
    return lines[0]


def _run(command: list[str], what: str) -> subprocess.CompletedProcess[str]:
    """Run ``command``, raising ``DoctorCheckError`` about ``what`` if it can't run or finish."""
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as err:
        raise errors.DoctorCheckError(
            f"Running {what} did not finish within {_TIMEOUT_SECONDS} seconds", _CC_HELP
        ) from err
    except OSError as err:
        raise errors.DoctorCheckError(f"Cannot run {what}: {err}", _CC_HELP) from err
