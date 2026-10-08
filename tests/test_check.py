# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import os
import pathlib
import subprocess

import pytest

from leech import codegen, diag, errors, mono, program, session
from tests import harness


def run_leech(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leech", *(str(a) for a in args)], capture_output=True, text=True, check=False, cwd=cwd
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

    proc = run_leech("check", root, cwd=tmp_path)

    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert listing(tmp_path) == before


def test_check_reports_type_error(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 { return true; }\n")

    proc = run_leech("check", root, cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr.startswith("ERROR: ")
    assert [path.name for path in tmp_path.iterdir()] == ["main.leech"]


def test_check_reports_warning_and_succeeds(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 { return 0; return 1; }\n")

    proc = run_leech("check", root)

    assert proc.returncode == 0
    assert proc.stderr.startswith("WARNING: return statement is unreachable\n")


def test_check_reports_struct_declaration_error(tmp_path):
    root = write(
        tmp_path / "main.leech",
        "struct S { s: S }\npub fn main() i32 { return 0; }\n",
    )

    proc = run_leech("check", root, cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.stderr.startswith('ERROR: Struct "S" has infinite size\n')
    assert [path.name for path in tmp_path.iterdir()] == ["main.leech"]


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


@pytest.mark.parametrize(
    ("src", "error"),
    (
        # Module variable initializers.
        ("let x = 1(2);", errors.NotCallableError),
        ("let x = { x };", errors.CircularVarInitializerError),
        ("struct S { a: i32 }\nlet x = S { b: 1 };", errors.InvalidStructFieldError),
        # Compile-time evaluation of an initializer.
        ("extern fn g() i32;\nlet x = g();", errors.CallExternFnAtComptimeError),
        ('let x: i32 = panic("no");', errors.PanicAtComptimeError),
        # Structs and unions, whether or not anything uses them.
        ("struct S { s: S }", errors.InfiniteSizeTypError),
        ("struct S { x: Missing }", errors.ItemNotFoundError),
        ("struct S[T] { x: S[T] }", errors.InfiniteSizeTypError),
        ("union U { A(Missing) }", errors.ItemNotFoundError),
        ("union U[T] { A(T, U[T]) }", errors.InfiniteSizeTypError),
        # Extern declarations, whether or not anything calls them.
        ("extern fn f(x: Missing);", errors.ItemNotFoundError),
        # Enums.
        ("enum E { A, A }", errors.DuplicateVariantInEnumDefnError),
        ("enum E(bool) { A }", errors.EnumBackingTypNotIntError),
        ("enum E(u8) { A = 300 }", errors.IntLitOverflowError),
    ),
)
def test_checking_reports_every_declaration_kind(compiler, src, error):
    with pytest.raises(error):
        compiler.build(f"{src}\npub fn main() i32 {{ return 0; }}")


def test_discovery_reports_each_failing_instance_and_continues(compiler):
    # Only loaded, so checking the declarations doesn't find these errors first. B[i32]
    # is requested only by h[i32]'s signature, and h[i32]'s own body fails too.
    mod = compiler.load(
        """
        struct A[T] { x: MissingA }
        struct B[T] { x: MissingB }
        fn h[T](b: *B[T]) i32 { return true; }
        pub fn f(a: *A[i32]) i32 { return 0; }
        pub fn g(b: *B[i32]) i32 { return h[i32](b); }
        """
    )

    result = mono.discover(mod.ctx)

    found = mod.ctx.diags.sorted()
    assert [type(d) for d in found] == [
        errors.ItemNotFoundError,
        errors.ItemNotFoundError,
        errors.InvalidRetTypError,
    ]
    assert ["MissingA" in d.message.message for d in found[:2]] == [True, False]
    assert result.struct_instances == ()
    assert _main_instance_names(result) == ["main::f", "main::g"]


def _main_instance_names(result: mono.MonoResult) -> list[str]:
    """The discovered function instances' names, apart from the bundled library's."""
    names = (inst.qualified_name for inst in result.fn_instances)
    return [name for name in names if not name.startswith("std::")]


def _check_main(tmp_path) -> pathlib.Path:
    return write(tmp_path / "main.leech", "pub fn main() i32 { return 0; }\n")


def test_user_error_raised_while_generating_ir_is_internal(tmp_path, monkeypatch):
    def fail(_compiler: codegen.Compiler) -> None:
        raise errors.CcNotFoundError("cc")

    monkeypatch.setattr(codegen.Compiler, "compile", fail)

    with pytest.raises(diag.InternalError, match="while generating IR"):
        program.Program(_check_main(tmp_path), entry=True).check(session.Session()).llvm_ir()


def test_user_error_emitted_while_generating_ir_is_internal(tmp_path, monkeypatch):
    compile_ = codegen.Compiler.compile

    def emit(compiler: codegen.Compiler) -> None:
        compile_(compiler)
        compiler._root.ctx.diags.error(errors.CcNotFoundError("cc"))

    monkeypatch.setattr(codegen.Compiler, "compile", emit)

    with pytest.raises(diag.InternalError, match="while generating IR"):
        program.Program(_check_main(tmp_path), entry=True).check(session.Session()).llvm_ir()


def test_check_generates_no_ir(tmp_path, monkeypatch):
    def fail(_program) -> str:
        raise AssertionError("check generated IR")

    monkeypatch.setattr(program.CheckedProgram, "llvm_ir", fail)
    root = write(tmp_path / "main.leech", "pub fn main() i32 { return 0; return 1; }\n")

    found = harness.check_program(root)

    assert [type(d) for d in found] == [errors.UnreachableCodeWarning]


def test_check_reports_unused_extern_signature_error(tmp_path):
    root = write(
        tmp_path / "main.leech", "extern fn f() Missing;\npub fn main() i32 { return 0; }\n"
    )

    assert [type(d) for d in harness.check_program(root)] == [errors.ItemNotFoundError]


def test_discovery_continues_past_a_failing_signature(compiler):
    mod = compiler.load("pub fn bad(x: Missing) {}\npub fn good() {}")

    result = mono.discover(mod.ctx)

    assert [type(d) for d in mod.ctx.diags.all()] == [errors.ItemNotFoundError]
    assert _main_instance_names(result) == ["main::good"]


def test_discovery_runs_despite_declaration_errors(compiler):
    src = "fn bad() i32 { return true; }\npub fn main() i32 { return 0; return 1; }"
    diags = diag.Diags()

    with pytest.raises(errors.InvalidRetTypError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.sorted()] == [
        errors.InvalidRetTypError,
        errors.UnreachableCodeWarning,
    ]
