# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import os
import pathlib
import subprocess


def run_leech(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leech", *(str(a) for a in args)], capture_output=True, text=True, check=False
    )


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def listing(directory: pathlib.Path) -> set[pathlib.Path]:
    return set(directory.rglob("*"))


def test_check_valid_program_writes_nothing(tmp_path):
    root = write(tmp_path / "main.leech", "import x::a;\npub fn main() i32 { return a::f(); }\n")
    write(tmp_path / "x" / "a.leech", "pub fn f() i32 { return 0; }\n")
    before = listing(tmp_path)

    proc = run_leech("check", root)

    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert listing(tmp_path) == before


def test_check_reports_type_error(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 { return true; }\n")

    proc = run_leech("check", root)

    assert proc.returncode == 1
    assert proc.stderr.startswith("ERROR: ")
    assert not (tmp_path / "leech-out").exists()


def test_check_reports_warning_and_succeeds(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 { return 0; return 1; }\n")

    proc = run_leech("check", root)

    assert proc.returncode == 0
    assert proc.stderr.startswith("WARNING: return statement is unreachable\n")


def test_check_reports_error_found_only_while_generating_code(tmp_path):
    # An infinite-size struct passes type checking, but generating its LLVM layout
    # rejects it, so this shows that check generates code.
    root = write(
        tmp_path / "main.leech",
        "struct S { s: S }\npub fn main() i32 { return 0; }\n",
    )

    proc = run_leech("check", root)

    assert proc.returncode == 1
    assert proc.stderr.startswith('ERROR: Struct "S" has infinite size\n')
    assert not (tmp_path / "leech-out").exists()


def test_check_reports_error_in_an_imported_module(tmp_path):
    root = write(tmp_path / "main.leech", "import lib;\npub fn main() i32 { return 0; }\n")
    write(tmp_path / "lib.leech", "struct S { s: S }\n")

    proc = run_leech("check", root)

    assert proc.returncode == 1
    assert proc.stderr.startswith('ERROR: Struct "S" has infinite size\n')


def test_check_needs_no_c_compiler(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 { return 0; }\n")

    proc = subprocess.run(
        ["leech", "check", str(root)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CC": "/nonexistent/cc"},
    )

    assert proc.returncode == 0, proc.stderr


def test_check_rejects_options_it_does_not_take(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 { return 0; }\n")

    proc = run_leech("check", root, "-O2")

    assert proc.returncode == 2
    assert "unrecognized arguments: -O2" in proc.stderr
