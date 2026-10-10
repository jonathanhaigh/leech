# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import os
import pathlib
import subprocess

import pytest

from leech import diag_kinds
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

    proc = run_leech("build", root, cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""
    result = run_exe(tmp_path / "hello")
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

    proc = run_leech("build", root, "--emit", "llvm-ir,exe", cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    definitions = [
        line
        for line in (tmp_path / "main.ll").read_text().splitlines()
        if line.startswith("define")
    ]
    for symbol in ("main::main", "x::a::g", "x::b::h", "std::io::println"):
        assert sum(f'@"{symbol}"(' in line for line in definitions) == 1
    result = run_exe(tmp_path / "main")
    assert (result.returncode, result.stdout) == (6, "multi\n")


def test_build_root_with_private_main_and_any_name(tmp_path):
    root = write(tmp_path / "tool.leech", "fn main() i32 { return 3; }\n")

    assert run_leech("build", root, cwd=tmp_path).returncode == 0

    assert run_exe(tmp_path / "tool").returncode == 3


def test_build_root_outside_working_directory(tmp_path):
    write(tmp_path / "sub" / "app.leech", _HELLO)

    proc = run_leech("build", "sub/app.leech", cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert run_exe(tmp_path / "app").stdout == "hello\n"
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["app.leech"]


def test_build_output_option(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)
    exe = tmp_path / "bin" / "greet"
    exe.parent.mkdir()

    assert run_leech("build", root, "-o", exe, cwd=tmp_path).returncode == 0

    assert run_exe(exe).stdout == "hello\n"
    assert not (tmp_path / "hello").exists()


def test_rebuild_replaces_the_executable(tmp_path):
    root = write(tmp_path / "app.leech", "import lib;\npub fn main() i32 { return lib::f(); }\n")
    write(tmp_path / "lib.leech", "pub fn f() i32 { return 1; }\n")
    assert run_leech("build", root, cwd=tmp_path).returncode == 0
    write(root, "pub fn main() i32 { return 2; }\n")

    assert run_leech("build", root, cwd=tmp_path).returncode == 0

    assert run_exe(tmp_path / "app").returncode == 2


def test_roots_in_one_directory_do_not_clobber_each_other(tmp_path):
    first = write(tmp_path / "first.leech", "pub fn main() i32 { return 1; }\n")
    second = write(tmp_path / "second.leech", "pub fn main() i32 { return 2; }\n")

    assert run_leech("build", first, cwd=tmp_path).returncode == 0
    assert run_leech("build", second, cwd=tmp_path).returncode == 0

    assert run_exe(tmp_path / "first").returncode == 1
    assert run_exe(tmp_path / "second").returncode == 2


def test_build_optimized(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    assert run_leech("build", root, "-O2", cwd=tmp_path).returncode == 0

    assert run_exe(tmp_path / "hello").stdout == "hello\n"


def test_compile_error_fails_without_executable(tmp_path):
    root = write(tmp_path / "bad.leech", "pub fn main() i32 { return true; }\n")

    proc = run_leech("build", root, cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr.startswith("ERROR: ")
    assert not (tmp_path / "bad").exists()


def test_missing_main_fails(tmp_path):
    root = write(tmp_path / "lib.leech", "pub fn f() i32 { return 0; }\n")

    proc = run_leech("build", root, cwd=tmp_path)

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

    before = set(tmp_path.iterdir())

    proc = run_leech("build", root, cwd=tmp_path)

    assert proc.returncode == 2
    assert proc.stderr.startswith("usage: leech build ")
    assert message in proc.stderr
    assert set(tmp_path.iterdir()) == before


def test_reserved_word_root_name_builds(tmp_path):
    root = write(tmp_path / "array.leech", "pub fn main() i32 { return 0; }\n")

    assert run_leech("build", root, cwd=tmp_path).returncode == 0


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

    proc = run_leech("build", root, cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.count("WARNING: unreachable return statement") == 2
    assert "1| pub fn v() i32 { return 1; return 2; }" in proc.stderr
    assert "2| pub fn u() i32 { return 3; return 4; }" in proc.stderr
    assert run_exe(tmp_path / "main").returncode == 4


@pytest.mark.parametrize("value", ("", "   "))
def test_blank_cc_means_cc(tmp_path, value):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc(value), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr


def test_missing_cc_is_reported_before_compiling(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc("/nonexistent/cc"), cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr == (
        'ERROR: cannot find C compiler "/nonexistent/cc"\n'
        "NOTE: install gcc or clang, or set CC to a C compiler\n"
    )
    assert [p.name for p in tmp_path.iterdir()] == ["hello.leech"]


def test_malformed_cc_is_reported(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc('cc "'), cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr == 'ERROR: invalid C compiler command "CC=cc "": No closing quotation\n'


def test_link_failure_is_reported_with_compiler_output(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)

    proc = run_leech("build", root, env=env_with_cc("cc -Wl,--no-such-flag"), cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr.startswith("ERROR: linking failed: `cc -Wl,--no-such-flag ")
    assert "exited with status" in proc.stderr
    assert "NOTE: the C compiler printed:\n" in proc.stderr
    assert "no-such-flag" in proc.stderr.split("NOTE: ", 1)[1]
    assert "Traceback" not in proc.stderr
    assert not (tmp_path / "hello").exists()


def test_version(tmp_path):
    proc = run_leech("--version")

    assert proc.returncode == 0
    assert proc.stdout.startswith("leech ")


def test_a_directory_destination_is_refused_before_anything_is_written(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)
    (tmp_path / "hello").mkdir()

    proc = run_leech("build", root, "--emit", "llvm-ir,exe", cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr == "ERROR: cannot write build output: hello is a directory\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["hello", "hello.leech"]


def test_default_output_that_is_a_directory_is_refused(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)
    (tmp_path / "hello").mkdir()

    proc = run_leech("build", root, cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr == "ERROR: cannot write build output: hello is a directory\n"


def test_linker_that_writes_nothing_fails_and_keeps_old_executable(tmp_path):
    root = write(tmp_path / "hello.leech", _HELLO)
    fake_cc = write(tmp_path / "fake-cc", "#!/bin/sh\nexit 0\n")
    fake_cc.chmod(0o755)
    old_exe = write(tmp_path / "hello", "old")

    proc = run_leech("build", root, env=env_with_cc(str(fake_cc)), cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr.startswith(f"ERROR: linking failed: `{fake_cc} ")
    assert "succeeded but wrote no executable" in proc.stderr
    assert old_exe.read_text() == "old"
    assert [p.name for p in old_exe.parent.iterdir() if "tmp" in p.name] == []


def test_builds_have_independent_diags(tmp_path):
    warned_root = write(tmp_path / "warned.leech", "pub fn main() i32 { return 0; return 1; }\n")
    root = write(tmp_path / "hello.leech", _HELLO)

    warned = harness.build_exe(warned_root)
    result = harness.build_exe(root)

    assert warned.exe is not None
    assert [d.kind for d in warned.diags] == [diag_kinds.UNREACHABLE_CODE]
    assert result.exe is not None
    assert result.diags == ()


def test_build_reports_a_warning_seen_by_several_compilations_once(tmp_path):
    root = write(tmp_path / "app.leech", "import helper;\npub fn main() i32 { helper::f() }\n")
    write(
        tmp_path / "helper.leech",
        "enum E { A }\npub fn f() i32 { return match (E::A) { E::A => 0i32, _ => 1i32, }; }\n",
    )

    result = harness.build_exe(root)

    assert result.exe is not None
    assert [d.kind for d in result.diags] == [diag_kinds.UNREACHABLE_MATCH_ARM]


def test_build_fails_on_emitted_error(tmp_path, monkeypatch):
    root = write(tmp_path / "hello.leech", _HELLO)
    harness.emit_error_while_checking(monkeypatch)

    result = harness.build_exe(root)

    assert result.exe is None
    assert [d.kind for d in result.diags] == [diag_kinds.MISSING_C_COMPILER]
    assert not (tmp_path / "hello").exists()
