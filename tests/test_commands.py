# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import argparse
import os
import pathlib
import subprocess
import sys

import pytest

from leech import diag_kinds, session, toolchain
from leech.cli import common
from leech.cli import leech as leech_cli

_HELLO = 'import std::io;\npub fn main() i32 { io::println("hello"); return 0; }\n'
_ICE = "internal compiler error"


def run_tool(*args, env=None, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(a) for a in args], capture_output=True, text=True, check=False, env=env, cwd=cwd
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
        argparse.Namespace(
            root=pathlib.Path("app.leech"),
            emit=frozenset({toolchain.OutputKind.EXE}),
            o=pathlib.Path("app"),
            opt_level=1,
        )
    )


@pytest.mark.parametrize(
    ("args", "outputs"),
    (
        ([], {toolchain.OutputKind.EXE: pathlib.Path("app")}),
        (["-o", "bin/x"], {toolchain.OutputKind.EXE: pathlib.Path("bin/x")}),
        (
            ["--emit", "asm,llvm-ir,asm"],
            {
                toolchain.OutputKind.ASM: pathlib.Path("app.s"),
                toolchain.OutputKind.LLVM_IR: pathlib.Path("app.ll"),
            },
        ),
    ),
)
def test_output_options_map_kinds_to_paths(args, outputs):
    group = common.OutputOptions()
    parser = argparse.ArgumentParser()
    group.add_arguments(parser)
    parsed = parser.parse_args(args)
    parsed.root = pathlib.Path("src/app.leech")

    group.validate(parser, parsed)

    assert parsed.outputs == outputs


@pytest.mark.parametrize(
    ("src", "cc", "expected"),
    (
        ("pub fn main() i32 { return true; }\n", None, "ERROR: return expression has type"),
        (_HELLO, "/nonexistent/cc", 'ERROR: cannot find C compiler "/nonexistent/cc"'),
        (_HELLO, "false", "ERROR: linking failed: `false "),
    ),
)
def test_build_failure_is_a_diagnostic_not_a_crash(tmp_path, src, cc, expected):
    root = write(tmp_path / "app.leech", src)
    env = os.environ.copy()
    if cc is not None:
        env["CC"] = cc

    proc = run_tool("leech", "build", root, env=env, cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr.startswith(expected)
    assert _ICE not in proc.stderr


def test_unwritable_build_output_is_a_diagnostic_not_a_crash(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)
    out = tmp_path / "out"
    out.mkdir()

    proc = run_tool("leech", "build", root, "-o", out, cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr == f"ERROR: cannot write build output: {out} is a directory\n"
    assert _ICE not in proc.stderr


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
    warning = "WARNING: unreachable return statement"
    assert stderr.count(warning) == 1
    assert stderr.index(warning) < stderr.index('ERROR: cannot run "')


def test_command_reports_an_already_reported_error_once(capsys):
    class _Failing(common.Command):
        name = "failing"
        help = description = "fail"

        def run(self, args, session):
            del args
            session.diags.raise_error(diag_kinds.MISSING_C_COMPILER, None, program="cc")

    assert _Failing().execute(argparse.Namespace(), "leech") == 1

    assert capsys.readouterr().err.count('ERROR: cannot find C compiler "cc"') == 1


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
    warning = "WARNING: unreachable return statement"
    assert stderr.count(warning) == 1
    assert stderr.index(warning) < stderr.index("ERROR: internal compiler error")


def test_output_in_a_missing_directory_is_a_diagnostic_not_a_crash(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_tool(
        "leech", "build", root, "--emit", "llvm-ir", "-o", tmp_path / "missing" / "app.ll"
    )

    assert proc.returncode == 1
    assert proc.stderr.startswith("ERROR: cannot write build output: ")
    assert _ICE not in proc.stderr
