# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import os
import pathlib
import subprocess

import pytest

from leech import build, errors
from tests import harness

_HELLO = 'import std::io;\npub fn main() i32 { io::println("hello"); return 0; }\n'


def run_leech(*args, cwd=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leech", *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=env,
    )


def run_exe(path: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run([path], capture_output=True, text=True, check=False)


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def env_with_cc(value: str) -> dict[str, str]:
    return {**os.environ, "CC": value}


def test_build_hello_world(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""
    result = run_exe(tmp_path / "leech-out" / "hello")
    assert (result.returncode, result.stdout) == (0, "hello\n")


def test_build_multi_module_program_with_std_modules(tmp_path):
    root = write(
        tmp_path / "app" / "main.leech",
        'import std::io;\nimport x::a;\nfn main() i32 { io::println("multi"); return a::g(); }\n',
    )
    write(
        tmp_path / "app" / "x" / "a.leech",
        "import std::mem;\nimport x::b;\n"
        "pub fn g() i32 {\n"
        "    let p = mem::alloc[i32]();\n"
        "    p.* = b::h();\n"
        "    let v = p.*;\n"
        "    mem::dealloc[i32](p);\n"
        "    return v;\n"
        "}\n",
    )
    write(tmp_path / "app" / "x" / "b.leech", "pub fn h() i32 { return 6; }\n")

    proc = run_leech("build", root)

    assert proc.returncode == 0, proc.stderr
    result = run_exe(tmp_path / "app" / "leech-out" / "main")
    assert (result.returncode, result.stdout) == (6, "multi\n")
    obj_dir = tmp_path / "app" / "leech-out" / "main.obj"
    assert {p.relative_to(obj_dir).as_posix() for p in obj_dir.rglob("*.ll")} == {
        "main.ll",
        "x/a.ll",
        "x/b.ll",
        "std/io.ll",
        "std/mem.ll",
        "std/prelude.ll",
    }


def test_build_root_with_private_main_and_any_name(tmp_path):
    root = write(tmp_path / "tool.leech", "fn main() i32 { return 3; }\n")

    assert run_leech("build", root).returncode == 0

    assert run_exe(tmp_path / "leech-out" / "tool").returncode == 3


def test_build_root_outside_working_directory(tmp_path):
    write(tmp_path / "sub" / "app.leech", _HELLO)

    proc = run_leech("build", "sub/app.leech", cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert run_exe(tmp_path / "sub" / "leech-out" / "app").stdout == "hello\n"
    assert not (tmp_path / "leech-out").exists()


def test_build_output_option(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)
    exe = tmp_path / "bin" / "greet"
    exe.parent.mkdir()

    assert run_leech("build", root, "-o", exe).returncode == 0

    assert run_exe(exe).stdout == "hello\n"
    assert not (tmp_path / "leech-out" / "hello").exists()


def test_build_output_dir_is_ignored_by_git_and_backups(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    assert run_leech("build", root).returncode == 0

    out_dir = tmp_path / "leech-out"
    assert (out_dir / ".gitignore").read_text() == "*\n"
    tag = (out_dir / "CACHEDIR.TAG").read_bytes()
    assert tag[:43] == b"Signature: 8a477f597d28d172789f06886806bc55"


def test_rebuild_replaces_stale_intermediates(tmp_path):
    root = write(tmp_path / "app.leech", "import lib;\npub fn main() i32 { return lib::f(); }\n")
    write(tmp_path / "lib.leech", "pub fn f() i32 { return 1; }\n")
    assert run_leech("build", root).returncode == 0
    write(root, "pub fn main() i32 { return 2; }\n")

    assert run_leech("build", root).returncode == 0

    assert run_exe(tmp_path / "leech-out" / "app").returncode == 2
    assert not (tmp_path / "leech-out" / "app.obj" / "lib.ll").exists()


def test_roots_in_one_directory_do_not_clobber_each_other(tmp_path):
    first = write(tmp_path / "first.leech", "pub fn main() i32 { return 1; }\n")
    second = write(tmp_path / "second.leech", "pub fn main() i32 { return 2; }\n")

    assert run_leech("build", first).returncode == 0
    assert run_leech("build", second).returncode == 0

    assert run_exe(tmp_path / "leech-out" / "first").returncode == 1
    assert run_exe(tmp_path / "leech-out" / "second").returncode == 2
    assert (tmp_path / "leech-out" / "first.obj" / "first.o").exists()


def test_build_optimized(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    assert run_leech("build", root, "-O2").returncode == 0

    assert run_exe(tmp_path / "leech-out" / "hello").stdout == "hello\n"


def test_compile_error_fails_without_executable(tmp_path):
    root = write(tmp_path / "bad.leech", "pub fn main() i32 { return true; }\n")

    proc = run_leech("build", root)

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr.startswith("ERROR: ")
    assert not (tmp_path / "leech-out" / "bad").exists()


def test_missing_main_fails(tmp_path):
    root = write(tmp_path / "lib.leech", "pub fn f() i32 { return 0; }\n")

    proc = run_leech("build", root)

    assert proc.returncode == 1
    assert 'has no "main" function' in proc.stderr


@pytest.mark.parametrize(
    ("name", "create", "message"),
    (
        ("missing.leech", False, "is not a file"),
        ("app.txt", True, "must end in '.leech'"),
        ("a::b.leech", True, "must be an identifier followed by '.leech'"),
        ("1app.leech", True, "must be an identifier followed by '.leech'"),
    ),
)
def test_invalid_root_is_a_usage_error(tmp_path, name, create, message):
    root = tmp_path / name
    if create:
        write(root, _HELLO)

    proc = run_leech("build", root)

    assert proc.returncode == 2
    assert proc.stderr.startswith("usage: leech build ")
    assert message in proc.stderr
    assert not (tmp_path / "leech-out").exists()


def test_reserved_word_root_name_builds(tmp_path):
    root = write(tmp_path / "array.leech", "pub fn main() i32 { return 0; }\n")

    assert run_leech("build", root).returncode == 0


def test_warning_from_a_shared_module_is_printed_once(tmp_path):
    root = write(
        tmp_path / "main.leech",
        "import a;\nimport b;\npub fn main() i32 { return a::f() + b::g(); }\n",
    )
    write(tmp_path / "a.leech", "import w;\npub fn f() i32 { return w::v(); }\n")
    write(tmp_path / "b.leech", "import w;\npub fn g() i32 { return w::u(); }\n")
    write(
        tmp_path / "w.leech",
        "pub fn v() i32 { return 1; return 2; }\npub fn u() i32 { return 3; return 4; }\n",
    )

    proc = run_leech("build", root)

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.count("WARNING: return statement is unreachable") == 2
    assert "1| pub fn v() i32 { return 1; return 2; }" in proc.stderr
    assert "2| pub fn u() i32 { return 3; return 4; }" in proc.stderr
    assert run_exe(tmp_path / "leech-out" / "main").returncode == 4


@pytest.mark.parametrize("value", ("", "   "))
def test_blank_cc_means_cc(tmp_path, value):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc(value))

    assert proc.returncode == 0, proc.stderr


def test_missing_cc_is_reported_before_compiling(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc("/nonexistent/cc"))

    assert proc.returncode == 1
    assert proc.stderr == (
        'ERROR: C compiler "/nonexistent/cc" not found; install gcc or clang, or set CC to a '
        "C compiler\n"
    )
    assert not (tmp_path / "leech-out").exists()


def test_malformed_cc_is_reported(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc('cc "'))

    assert proc.returncode == 1
    assert (
        proc.stderr == "ERROR: The C compiler command CC='cc \"' is invalid: No closing quotation\n"
    )


def test_link_failure_is_reported_with_compiler_output(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc("cc -Wl,--no-such-flag"))

    assert proc.returncode == 1
    assert proc.stderr.startswith("ERROR: Linking failed: `cc -Wl,--no-such-flag ")
    assert "exited with status" in proc.stderr
    assert "NOTE: " in proc.stderr
    assert "no-such-flag" in proc.stderr.split("NOTE: ", 1)[1]
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "leech-out" / "hello").exists()


def test_version(tmp_path):
    proc = run_leech("--version")

    assert proc.returncode == 0
    assert proc.stdout.startswith("leech ")


def test_symlinked_output_dir_is_refused(tmp_path):
    root = write(tmp_path / "app" / "hello.leech", _HELLO)
    elsewhere = tmp_path / "elsewhere"
    sentinel = write(elsewhere / "hello.obj" / "keep.txt", "keep")
    (tmp_path / "app" / "leech-out").symlink_to(elsewhere, target_is_directory=True)

    proc = run_leech("build", root)

    assert proc.returncode == 1
    assert proc.stderr == (
        f"ERROR: Cannot write build output: {tmp_path / 'app' / 'leech-out'} exists but is "
        "not a directory\n"
    )
    assert sentinel.read_text() == "keep"


def test_output_dir_that_is_a_file_is_refused(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)
    write(tmp_path / "leech-out", "not a directory")

    proc = run_leech("build", root)

    assert proc.returncode == 1
    assert "exists but is not a directory" in proc.stderr


def test_linker_that_writes_nothing_fails_and_keeps_old_executable(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)
    fake_cc = write(tmp_path / "fake-cc", "#!/bin/sh\nexit 0\n")
    fake_cc.chmod(0o755)
    old_exe = write(tmp_path / "leech-out" / "hello", "old")

    proc = run_leech("build", root, env=env_with_cc(str(fake_cc)))

    assert proc.returncode == 1
    assert proc.stderr.startswith(f"ERROR: Linking failed: `{fake_cc} ")
    assert "succeeded but wrote no executable" in proc.stderr
    assert old_exe.read_text() == "old"
    assert [p.name for p in old_exe.parent.iterdir() if "tmp" in p.name] == []


def test_builds_have_independent_diags(tmp_path):
    warned_root = write(tmp_path / "warned.leech", "pub fn main() i32 { return 0; return 1; }\n")
    root = write(tmp_path / "hello.leech", _HELLO)

    warned = build.build(warned_root)
    result = build.build(root)

    assert warned.exe is not None
    assert [type(d) for d in warned.diags] == [errors.UnreachableCodeWarning]
    assert result.exe is not None
    assert result.diags == ()


def test_build_reports_a_warning_seen_by_several_compilations_once(tmp_path):
    root = write(tmp_path / "app.leech", "import helper;\npub fn main() i32 { helper::f() }\n")
    write(
        tmp_path / "helper.leech",
        "enum E { A }\npub fn f() i32 { return match (E::A) { E::A => 0i32, _ => 1i32, }; }\n",
    )

    result = build.build(root)

    assert result.exe is not None
    assert [type(d) for d in result.diags] == [errors.UnreachableMatchArmWarning]


def test_build_fails_on_emitted_error(tmp_path, monkeypatch):
    root = write(tmp_path / "hello.leech", _HELLO)
    harness.emit_error_after_lowering(monkeypatch)

    result = build.build(root)

    assert result.exe is None
    assert [type(d) for d in result.diags] == [errors.CcNotFoundError]
    assert not (tmp_path / "leech-out").exists()
