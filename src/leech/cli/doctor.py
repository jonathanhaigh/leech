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
from typing import Final, TextIO, override

from leech import diag, diag_kinds, toolchain
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
            cc = list(toolchain.Linker.from_env(session.diags).command)
        except diag.ReportedError:
            return self._fail(report, "C compiler", session)
        report.write(f"C compiler: {shlex.join(cc)} ({shutil.which(cc[0])})\n")
        try:
            version = _cc_version(cc, session.diags)
        except diag.ReportedError:
            return self._fail(report, "C compiler version", session)
        report.write(f"  {version}\n")
        with tempfile.TemporaryDirectory(prefix="leech-doctor-") as tmp_dir:
            root = pathlib.Path(tmp_dir) / "doctor.leech"
            root.write_text(_TEST_PROGRAM)
            exe = pathlib.Path(tmp_dir) / "doctor"
            built = False
            with common.suppressing_reported_errors():
                common.build(root, {toolchain.OutputKind.EXE: exe}, session)
                built = True
            if not built:
                linking = any(d.kind is diag_kinds.LINK_FAILURE for d in session.diags.all())
                return self._fail(report, "Build a test program", session, fix=linking)
            try:
                proc = _run([str(exe)], "the test program", session.diags)
            except diag.ReportedError:
                return self._fail(report, "Run the test program", session)
            if proc.returncode != 0 or proc.stdout != _TEST_OUTPUT:
                session.diags.error(
                    _check_failure(
                        f"the test program exited with status {proc.returncode} and printed "
                        f"{proc.stdout!r}, expecting status 0 and {_TEST_OUTPUT!r}"
                    )
                )
                return self._fail(report, "Run the test program", session)
        report.write("Build and run a test program: ok\n")
        return True

    def _fail(
        self,
        report: TextIO,
        check: str,
        session: session_mod.Session,
        *,
        fix: bool = False,
    ) -> bool:
        """Report ``check`` as failed, followed by the session's diagnostics.

        With ``fix``, a note on fixing the C compiler is reported to the session too, and follows
        them.
        """
        report.write(f"{check}: FAILED\n")
        report.flush()
        if fix:
            session.diags.note(diag_kinds.C_COMPILER_HINT, None)
        self.render_diags(session)
        return False


def _check_failure(problem: str) -> diag.Diag:
    return diag.Diag.new(diag_kinds.TOOLCHAIN_CHECK_FAILURE, None, problem=problem).with_note(
        diag_kinds.CHECK_CC
    )


def _cc_version(cc: list[str], diags: diag.Diags) -> str:
    """Return the first line ``cc --version`` prints, reporting a failure to ``diags``."""
    command = shlex.join([*cc, "--version"])
    proc = _run([*cc, "--version"], f"`{command}`", diags)
    lines = proc.stdout.splitlines()
    if proc.returncode != 0 or not lines:
        diags.raise_error(
            _check_failure(
                f"`{command}` exited with status {proc.returncode} and printed no version"
            )
        )
    return lines[0]


def _run(command: list[str], what: str, diags: diag.Diags) -> subprocess.CompletedProcess[str]:
    """Run ``command``, reporting to ``diags`` a failure to run or finish ``what``."""
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        diags.raise_error(
            _check_failure(f"running {what} did not finish within {_TIMEOUT_SECONDS} seconds")
        )
    except OSError as err:
        diags.raise_error(_check_failure(f"cannot run {what}: {err}"))
