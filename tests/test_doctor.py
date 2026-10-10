# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import argparse
import importlib.metadata
import os
import pathlib
import platform
import subprocess

import pytest

from leech import ir_loader
from leech.cli import doctor


def run_doctor(cc=None, cwd=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if cc is not None:
        env["CC"] = cc
    return subprocess.run(
        ["leech", "doctor"], capture_output=True, text=True, check=False, env=env, cwd=cwd
    )


def fake_cc(tmp_path: pathlib.Path, script: str) -> pathlib.Path:
    path = tmp_path / "fake-cc"
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)
    return path


def test_doctor_passes_with_working_toolchain():
    proc = run_doctor()

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    lines = proc.stdout.splitlines()
    assert lines[0].startswith(f"leech {importlib.metadata.version('leech')} (LLVM ")
    assert lines[1] == f"Python {platform.python_version()}"
    assert lines[2].startswith("C compiler: cc (")
    assert lines[3].startswith("  ")
    assert lines[4:] == ["Build and run a test program: ok"]


def test_doctor_writes_nothing_in_the_current_directory(tmp_path):
    proc = run_doctor(cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert list(tmp_path.iterdir()) == []


def test_doctor_reports_missing_cc():
    proc = run_doctor("/nonexistent/cc")

    assert proc.returncode == 1
    assert proc.stdout.endswith("C compiler: FAILED\n")
    assert proc.stderr == (
        'ERROR: C compiler "/nonexistent/cc" not found; install gcc or clang, or set CC to a '
        "C compiler\n"
    )


def test_doctor_reports_malformed_cc():
    proc = run_doctor('cc "')

    assert proc.returncode == 1
    assert proc.stdout.endswith("C compiler: FAILED\n")
    assert (
        proc.stderr == "ERROR: The C compiler command CC='cc \"' is invalid: No closing quotation\n"
    )


def test_doctor_reports_failing_cc_version(tmp_path):
    cc = fake_cc(tmp_path, "exit 1\n")

    proc = run_doctor(str(cc))

    assert proc.returncode == 1
    assert proc.stdout.endswith("C compiler version: FAILED\n")
    assert proc.stderr == (
        f"ERROR: `{cc} --version` exited with status 1 and printed no version\n"
        "NOTE: Check that CC names a C compiler that can link programs, such as gcc\n"
    )


def test_doctor_reports_failing_link(tmp_path):
    cc = fake_cc(tmp_path, 'if [ "$1" = --version ]; then echo fake 1.0; fi\n')

    proc = run_doctor(str(cc))

    assert proc.returncode == 1
    assert "  fake 1.0\n" in proc.stdout
    assert proc.stdout.endswith("Build a test program: FAILED\n")
    assert proc.stderr.startswith(f"ERROR: Linking failed: `{cc} ")
    assert "succeeded but wrote no executable" in proc.stderr
    assert proc.stderr.endswith(
        "NOTE: Check that CC names a C compiler that can link programs, such as gcc\n"
    )


def test_doctor_reports_misbehaving_test_program(tmp_path):
    # A "linker" that writes an executable printing the wrong output.
    cc = fake_cc(
        tmp_path,
        'if [ "$1" = --version ]; then echo fake 1.0; exit 0; fi\n'
        'while [ "$1" != -o ]; do shift; done\n'
        "printf '#!/bin/sh\\necho wrong\\n' > \"$2\"\n"
        'chmod +x "$2"\n',
    )

    proc = run_doctor(str(cc))

    assert proc.returncode == 1
    assert proc.stdout.endswith("Run the test program: FAILED\n")
    assert proc.stderr == (
        "ERROR: The test program exited with status 0 and printed 'wrong\\n', expecting "
        "status 0 and "
        "'leech doctor\\n'\n"
        "NOTE: Check that CC names a C compiler that can link programs, such as gcc\n"
    )


def test_doctor_reports_test_program_that_cannot_run(tmp_path):
    # A "linker" that writes the output file without making it executable.
    cc = fake_cc(
        tmp_path,
        'if [ "$1" = --version ]; then echo fake 1.0; exit 0; fi\n'
        'while [ "$1" != -o ]; do shift; done\n'
        "echo 'echo wrong' > \"$2\"\n",
    )

    proc = run_doctor(str(cc))

    assert proc.returncode == 1
    assert proc.stdout.endswith("Run the test program: FAILED\n")
    assert proc.stderr.startswith(
        "ERROR: Cannot run the test program: [Errno 13] Permission denied"
    )
    assert "Traceback" not in proc.stderr
    assert proc.stderr.endswith(
        "NOTE: Check that CC names a C compiler that can link programs, such as gcc\n"
    )


def test_doctor_reports_cc_version_that_prints_nothing(tmp_path):
    cc = fake_cc(tmp_path, "exit 0\n")

    proc = run_doctor(str(cc))

    assert proc.returncode == 1
    assert proc.stdout.endswith("C compiler version: FAILED\n")
    assert f"`{cc} --version` exited with status 0 and printed no version" in proc.stderr


def test_doctor_reports_hanging_test_program(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_TIMEOUT_SECONDS", 1)
    cc = fake_cc(
        tmp_path,
        'if [ "$1" = --version ]; then echo fake 1.0; exit 0; fi\n'
        'while [ "$1" != -o ]; do shift; done\n'
        "printf '#!/bin/sh\\nsleep 30\\n' > \"$2\"\n"
        'chmod +x "$2"\n',
    )
    monkeypatch.setenv("CC", str(cc))

    assert doctor.DoctorCommand().execute(argparse.Namespace(), "leech") == 1

    captured = capsys.readouterr()
    assert captured.out.endswith("Run the test program: FAILED\n")
    assert captured.err.startswith(
        "ERROR: Running the test program did not finish within 1 seconds\n"
    )


def test_doctor_renders_build_diagnostics_before_a_crash(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_TEST_PROGRAM", "pub fn main() i32 { return true; }\n")
    check = ir_loader.ModLoader.check_declarations

    def check_then_crash(loader: ir_loader.ModLoader) -> None:
        check(loader)
        raise RuntimeError("boom")

    monkeypatch.setattr(ir_loader.ModLoader, "check_declarations", check_then_crash)

    with pytest.raises(RuntimeError, match="boom"):
        doctor.DoctorCommand().execute(argparse.Namespace(), "leech")

    stderr = capsys.readouterr().err
    user_error = stderr.index("ERROR: return expression has type")
    ice = stderr.index("ERROR: internal compiler error: RuntimeError: boom")
    assert user_error < ice
