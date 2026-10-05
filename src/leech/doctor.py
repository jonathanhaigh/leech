# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Diagnosing whether the toolchain can build and run Leech programs."""

import pathlib
import platform
import shlex
import shutil
import subprocess
import tempfile
from typing import Final, TextIO

from leech import build, driver, errors

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


def diagnose(report: TextIO) -> bool:
    """Write versions and toolchain checks to ``report``; return whether every check passed.

    Problems are rendered as diagnostics, with a note on how to fix those caused by the C
    compiler.
    """
    report.write(f"{driver.version_text('leech')}\n")
    report.write(f"Python {platform.python_version()}\n")
    try:
        cc = build.resolve_cc()
    except errors.UserError as err:
        return _fail(report, "C compiler", [err])
    report.write(f"C compiler: {shlex.join(cc)} ({shutil.which(cc[0])})\n")
    try:
        version = _cc_version(cc)
    except errors.DoctorCheckError as err:
        return _fail(report, "C compiler version", [err])
    report.write(f"  {version}\n")
    with tempfile.TemporaryDirectory(prefix="leech-doctor-") as tmp_dir:
        root = pathlib.Path(tmp_dir) / "doctor.leech"
        root.write_text(_TEST_PROGRAM)
        result = build.build(root)
        if result.exe is None:
            diags = list(result.diags)
            if any(isinstance(diag, errors.LinkFailedError) for diag in diags):
                diags.append(errors.DoctorFixNote(_CC_HELP))
            return _fail(report, "Build a test program", diags)
        try:
            proc = _run([str(result.exe)], "the test program")
        except errors.DoctorCheckError as err:
            return _fail(report, "Run the test program", [err])
        if proc.returncode != 0 or proc.stdout != _TEST_OUTPUT:
            problem = errors.DoctorCheckError(
                f"The test program exited with status {proc.returncode} and printed "
                f"{proc.stdout!r}, expecting status 0 and {_TEST_OUTPUT!r}",
                _CC_HELP,
            )
            return _fail(report, "Run the test program", [problem])
    report.write("Build and run a test program: ok\n")
    return True


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


def _fail(report: TextIO, check: str, diags: list[errors.UserError]) -> bool:
    report.write(f"{check}: FAILED\n")
    report.flush()
    errors.TextErrorRenderer().display_errors(diags)
    return False
