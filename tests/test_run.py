# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import os
import pathlib
import signal
import subprocess
import sys

import pytest

from leech.cli import common
from leech.cli import leech as leech_cli

_HELLO = 'import std::io;\npub fn main() i32 { io::println("hello"); return 0; }\n'


def run_leech(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leech", *(str(a) for a in args)], capture_output=True, text=True, check=False, cwd=cwd
    )


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class _ExecInterceptedError(Exception):
    def __init__(self, path, argv) -> None:
        super().__init__(path, argv)
        self.path = path
        self.argv = argv


@pytest.fixture
def exec_calls(monkeypatch):
    """Record ``leech run`` builds and stop where it would replace the process."""
    builds = []
    real_build = common.build_exe

    def recording_build(root, output, session):
        builds.append({"output": output, "opt_level": session.opt_level})
        return real_build(root, output, session)

    def fake_execv(path, argv):
        raise _ExecInterceptedError(path, argv)

    monkeypatch.setattr(common, "build_exe", recording_build)
    monkeypatch.setattr(os, "execv", fake_execv)
    return builds


def run_in_process(monkeypatch, *args) -> _ExecInterceptedError:
    monkeypatch.setattr(sys, "argv", ["leech", *(str(a) for a in args)])
    with pytest.raises(_ExecInterceptedError) as exc_info:
        leech_cli.main()
    return exc_info.value


def test_run_forwards_output_and_exit_status(tmp_path):
    root = write(
        tmp_path / "app.leech",
        'import std::io;\nfn main() i32 { io::println("out"); return 3; }\n',
    )

    proc = run_leech("run", root)

    assert (proc.returncode, proc.stdout, proc.stderr) == (3, "out\n", "")


def test_run_forwards_termination_signal(tmp_path):
    root = write(tmp_path / "app.leech", 'pub fn main() i32 { panic("boom"); }\n')

    proc = run_leech("run", root)

    assert proc.returncode == -signal.SIGABRT
    assert proc.stderr == "boom\n"


def test_run_prints_build_warnings_before_running(tmp_path):
    root = write(tmp_path / "app.leech", "pub fn main() i32 { return 0; return 1; }\n")

    proc = run_leech("run", root)

    assert proc.returncode == 0
    assert proc.stderr.startswith("WARNING: return statement is unreachable\n")


def test_run_root_outside_working_directory(tmp_path):
    write(tmp_path / "sub" / "app.leech", _HELLO)

    proc = run_leech("run", "sub/app.leech", cwd=tmp_path)

    assert (proc.returncode, proc.stdout) == (0, "hello\n")
    assert (tmp_path / "sub" / "leech-out" / "app").exists()


def test_run_build_failure_runs_nothing(tmp_path):
    root = write(tmp_path / "app.leech", "pub fn main() i32 { return true; }\n")

    proc = run_leech("run", root)

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr.startswith("ERROR: ")


def test_separator_is_rejected_by_build(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_leech("build", root, "--", "a")

    assert proc.returncode == 2
    assert "arguments after '--' are only accepted by 'leech run'" in proc.stderr


@pytest.mark.parametrize(
    ("args", "opt_level", "program_args"),
    (
        (("{root}", "-O2", "--", "a", "b"), 2, ["a", "b"]),
        (("-O2", "{root}"), 2, []),
        (("{root}", "--", "-O3", "x"), 0, ["-O3", "x"]),
        (("{root}", "--", "--", "-h"), 0, ["--", "-h"]),
    ),
)
def test_run_arguments(tmp_path, monkeypatch, exec_calls, args, opt_level, program_args):
    root = write(tmp_path / "sub" / "app.leech", _HELLO)
    monkeypatch.chdir(tmp_path)

    intercepted = run_in_process(
        monkeypatch, "run", *(a.format(root="sub/app.leech") for a in args)
    )

    assert [call["opt_level"] for call in exec_calls] == [opt_level]
    exe = (root.parent / "leech-out" / "app").absolute()
    assert intercepted.path == str(exe)
    assert intercepted.argv == [str(exe), *program_args]


def test_run_restores_default_handling_of_python_ignored_signals(tmp_path):
    # The program writes until its closed stdout makes a write raise SIGPIPE. Python
    # ignores SIGPIPE, so without restoring the default the program would see EPIPE and
    # finish normally instead.
    root = write(
        tmp_path / "app.leech",
        "import std::io;\n"
        "pub fn main() i32 {\n"
        "    let mut i = 0;\n"
        "    while (i < 1000000) {\n"
        '        io::println("line");\n'
        "        i = i + 1;\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
    )
    with subprocess.Popen(
        ["leech", "run", str(root)], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as proc:
        assert proc.stdout is not None
        proc.stdout.close()
        proc.wait(timeout=60)

    assert proc.returncode == -signal.SIGPIPE


def test_run_reports_exec_failure_without_traceback(tmp_path, monkeypatch, capsys):
    root = write(tmp_path / "app.leech", _HELLO)

    def failing_execv(path, argv):
        del argv
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "execv", failing_execv)
    monkeypatch.setattr(sys, "argv", ["leech", "run", str(root)])

    with pytest.raises(SystemExit) as exc_info:
        leech_cli.main()

    assert exc_info.value.code == 1
    assert signal.getsignal(signal.SIGPIPE) == signal.SIG_IGN
    exe = (tmp_path / "leech-out" / "app").absolute()
    assert capsys.readouterr().err == (
        f"ERROR: Cannot run {exe}: [Errno 13] Permission denied: '{exe}'\n"
    )
