# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import argparse
import os
import pathlib
import subprocess
import sys

import pytest

from leech import diag, errors, session
from leech.cli import common
from leech.cli import leech as leech_cli

_HELLO = 'import std::io;\npub fn main() i32 { io::println("hello"); return 0; }\n'
_ICE = "internal compiler error"


def run_tool(*args, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args], capture_output=True, text=True, check=False, env=env
    )


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_session_defaults():
    compiler_session = session.Session()

    assert compiler_session.diags.all() == ()
    assert compiler_session.opt_level == 0
    assert session.Session().diags is not compiler_session.diags


def test_optimization_options_configure_the_session():
    group = common.OptimizationOptions()
    parser = argparse.ArgumentParser()
    group.add_arguments(parser)
    compiler_session = session.Session()

    group.configure(parser.parse_args(["-O2"]), compiler_session)

    assert compiler_session.opt_level == 2


def test_every_command_has_a_parser():
    commands = [command_type() for command_type in leech_cli.COMMANDS]

    _, command_parsers = leech_cli.make_parser(commands)

    assert list(command_parsers) == ["build", "run", "check", "doctor"]
    assert command_parsers["build"].parse_args(["app.leech", "-O1", "-o", "app"]) == (
        argparse.Namespace(root=pathlib.Path("app.leech"), opt_level=1, o=pathlib.Path("app"))
    )


@pytest.mark.parametrize(
    ("src", "cc", "expected"),
    (
        ("pub fn main() i32 { return true; }\n", None, "ERROR: Return expression has invalid"),
        (_HELLO, "/nonexistent/cc", 'ERROR: C compiler "/nonexistent/cc" not found'),
        (_HELLO, "false", "ERROR: Linking failed: `false "),
    ),
)
def test_build_failure_is_a_diagnostic_not_a_crash(tmp_path, src, cc, expected):
    root = write(tmp_path / "app.leech", src)
    env = os.environ.copy()
    if cc is not None:
        env["CC"] = cc

    proc = run_tool("leech", "build", root, env=env)

    assert proc.returncode == 1
    assert proc.stderr.startswith(expected)
    assert _ICE not in proc.stderr


def test_unwritable_build_output_is_a_diagnostic_not_a_crash(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)
    out = tmp_path / "out"
    out.mkdir()

    proc = run_tool("leech", "build", root, "-o", out)

    assert proc.returncode == 1
    assert proc.stderr.startswith("ERROR: Cannot write build output: ")
    assert _ICE not in proc.stderr


def test_leechc_compiles_only_its_module(tmp_path):
    # Only the imported module's own compilation lowers its public functions, which is
    # where the unreachable code is found.
    root = write(tmp_path / "app.leech", "import a;\npub fn main() i32 { return 0; }\n")
    write(tmp_path / "a.leech", "pub fn f() i32 { return 0; return 1; }\n")

    leechc = run_tool("leechc", root, "--entry", "-o", tmp_path / "app.ll")
    build = run_tool("leech", "build", root, "-o", tmp_path / "app")

    assert (leechc.returncode, leechc.stderr) == (errors.NOTE, "")
    assert build.returncode == 0
    assert build.stderr.startswith("WARNING: return statement is unreachable")


def test_run_renders_build_diagnostics_once(tmp_path, monkeypatch, capsys):
    root = write(tmp_path / "app.leech", "pub fn main() i32 { return 0; return 1; }\n")

    def failing_execv(path, argv):
        del argv
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "execv", failing_execv)
    monkeypatch.setattr(sys, "argv", ["leech", "run", str(root)])

    with pytest.raises(SystemExit) as exc_info:
        leech_cli.main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    warning = "WARNING: return statement is unreachable"
    assert stderr.count(warning) == 1
    assert stderr.index(warning) < stderr.index("ERROR: Cannot run ")


def test_command_reports_a_raised_user_error(capsys):
    class _Failing(common.Command):
        name = "failing"
        help = description = "fail"

        def run(self, args, session):
            del args, session
            raise errors.CcNotFoundError("cc")

    assert _Failing().execute(argparse.Namespace(), "leech") == 1

    assert capsys.readouterr().err.startswith('ERROR: C compiler "cc" not found')


def test_command_reports_an_already_reported_error_once(capsys):
    class _Failing(common.Command):
        name = "failing"
        help = description = "fail"

        def run(self, args, session):
            del args
            raise diag.ReportedError(session.diags.error(errors.CcNotFoundError("cc")))

    assert _Failing().execute(argparse.Namespace(), "leech") == 1

    assert capsys.readouterr().err.count("ERROR: C compiler") == 1


def test_crash_after_run_renders_build_diagnostics_once(tmp_path, monkeypatch, capsys):
    root = write(tmp_path / "app.leech", "pub fn main() i32 { return 0; return 1; }\n")

    def crash(exe, args):
        del exe, args
        raise RuntimeError("boom")

    monkeypatch.setattr(leech_cli, "_exec_natively", crash)
    monkeypatch.setattr(sys, "argv", ["leech", "run", str(root)])

    with pytest.raises(RuntimeError, match="boom"):
        leech_cli.main()

    stderr = capsys.readouterr().err
    warning = "WARNING: return statement is unreachable"
    assert stderr.count(warning) == 1
    assert stderr.index(warning) < stderr.index("ERROR: internal compiler error")


def test_leechc_unwritable_output_is_a_diagnostic_not_a_crash(tmp_path):
    root = write(tmp_path / "app.leech", "pub fn f() i32 { return 0; }\n")

    proc = run_tool("leechc", root, "-o", tmp_path / "missing" / "app.ll")

    assert proc.returncode == errors.ERROR
    assert proc.stderr.startswith("ERROR: Cannot write build output: ")
    assert _ICE not in proc.stderr
