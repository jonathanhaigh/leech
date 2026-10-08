# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
import sys

import pytest

from leech import build, cli, diag, driver, errors, ir_loader, opt_util
from tests import harness


def test_errors_in_separate_bodies_are_all_reported_in_source_order(compiler):
    # `a` forces the struct's field types, so the struct's error is found first,
    # but it is reported after `b`'s, which comes before it in the source.
    src = """
    fn a() i32 { let s = S { x: 1 }; return 0; }
    fn b() i32 { return true; }
    struct S { x: Missing }
    """
    diags = diag.Diags()

    with pytest.raises(errors.InvalidRetTypError):
        compiler.build(src, diags=diags)

    found = diags.sorted()
    assert [type(d) for d in found] == [errors.InvalidRetTypError, errors.ItemNotFoundError]
    harness.assert_span_at(found[0].message.span, src, "true")
    harness.assert_span_at(found[1].message.span, src, "Missing")


def test_broken_struct_used_by_several_functions_is_reported_once(compiler):
    src = """
    struct S { x: Missing }
    fn a() i32 { let s = S { x: 1 }; return 0; }
    fn b(s: S) i32 { return 0; }
    fn c() i32 { let s = S { x: 2 }; return 1; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.ItemNotFoundError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.ItemNotFoundError]


def test_broken_signature_used_by_several_callers_is_reported_once(compiler):
    src = """
    fn f(x: Missing) i32 { return 0; }
    fn a() i32 { return f(1); }
    fn b() i32 { return f(2); }
    """
    diags = diag.Diags()

    with pytest.raises(errors.ItemNotFoundError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.ItemNotFoundError]


def test_initializer_error_and_body_error_are_both_reported(compiler):
    src = """
    let g: i32 = true;
    fn uses_g() i32 { return g; }
    fn other() i32 { return true; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.IncompatibleLetTypError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.sorted()] == [
        errors.IncompatibleLetTypError,
        errors.InvalidRetTypError,
    ]


def test_errors_in_root_and_imported_modules_follow_load_order(compiler):
    program = harness.TestProgram.from_main(
        "import a;\nfn r() i32 { return true; }\n",
        harness.ModSrc("a", "pub fn f() i32 { return true; }\n"),
    )
    diags = diag.Diags()

    with pytest.raises(errors.InvalidRetTypError):
        compiler.build(program, diags=diags)

    paths = [opt_path(d) for d in diags.sorted()]
    assert paths == [compiler.workspace / "main.leech", compiler.workspace / "a.leech"]


def opt_path(err: errors.UserError) -> pathlib.Path:
    span = err.message.span
    assert span is not None
    return span.file.path


def test_duplicate_definition_is_reported_once_and_its_body_is_not_checked(compiler):
    src = """
    fn f() i32 { return 0; }
    fn f() i32 { return true; }
    fn g() i32 { return f(); }
    """
    diags = diag.Diags()

    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.DuplicateItemDefnError]


def test_rejected_name_is_poisoned_so_uses_report_nothing_more(compiler):
    src = """
    fn i32() i32 { return 0; }
    fn g() i32 { return i32(); }
    """
    diags = diag.Diags()

    with pytest.raises(errors.ReservedNameError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.ReservedNameError]


def test_rejected_import_is_poisoned_so_uses_report_nothing_more(compiler):
    src = """
    import missing;
    fn g() i32 { return missing::f(); }
    fn h() i32 { return true; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.ModDoesNotExistError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.sorted()] == [
        errors.ModDoesNotExistError,
        errors.InvalidRetTypError,
    ]


def test_incomplete_impl_is_rejected_without_registering(compiler):
    # The first impl is rejected before it is registered, so the second one doesn't
    # conflict with it, and the call resolves through the second.
    src = """
    trait T { fn a(*self) i32; fn b(*self) i32; }
    struct S { x: i32 }
    impl T for S { fn a(*self) i32 { return 1; } }
    impl T for S { fn a(*self) i32 { return 1; } fn b(*self) i32 { return 2; } }
    fn main() i32 { let s = S { x: 1 }; return s.a(); }
    """
    diags = diag.Diags()

    with pytest.raises(errors.TraitMethodNotImplementedError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.TraitMethodNotImplementedError]


def test_rejected_item_leaves_no_comptime_parameter_to_check(compiler):
    src = """
    fn f() i32 { return 0; }
    fn f[T: Missing](x: T) i32 { return 0; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.DuplicateItemDefnError]


def test_mutually_recursive_initializers_are_reported_once(compiler):
    src = """
    let a: i32 = b;
    let b: i32 = a;
    fn f() i32 { return a; }
    fn g() i32 { return b; }
    fn h() i32 { return true; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.CircularVarInitializerError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.sorted()] == [
        errors.CircularVarInitializerError,
        errors.InvalidRetTypError,
    ]


def test_mutually_infinite_structs_are_reported_once(compiler):
    src = """
    struct A { b: B }
    struct B { a: A }
    pub fn f() i32 { return 0; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.InfiniteSizeTypError):
        compiler.compile(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.InfiniteSizeTypError]


def test_mutually_recursive_trait_bounds_are_reported_once(compiler):
    src = """
    trait A[T: B[T]] { fn a(*self) T; }
    trait B[T: A[T]] { fn b(*self) T; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.RecursiveTraitBoundError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.RecursiveTraitBoundError]


def test_build_reports_later_compilations_diagnostics_in_load_order(tmp_path):
    # Each compilation lowers only its own module's non-generic functions, so a's
    # warning is found by a's own compilation, after the root's.
    root = tmp_path / "app.leech"
    root.write_text("import a;\npub fn main() i32 { return 0; return 1; }\n")
    (tmp_path / "a.leech").write_text("pub fn f() i32 { return 0; return 1; }\n")

    result = build.build(root)

    assert result.exe is not None
    spans = [opt_util.opt_unwrap(d.message.span) for d in result.diags]
    assert [type(d) for d in result.diags] == [errors.UnreachableCodeWarning] * 2
    assert [span.file.path.name for span in spans] == ["app.leech", "a.leech"]


def _crash_after_checking(monkeypatch) -> None:
    """Make declaration checking crash after it has reported its errors."""
    check = ir_loader.ModLoader.check_declarations

    def check_then_crash(loader: ir_loader.ModLoader) -> None:
        check(loader)
        raise RuntimeError("boom")

    monkeypatch.setattr(ir_loader.ModLoader, "check_declarations", check_then_crash)


@pytest.mark.parametrize(
    ("argv", "tool"),
    ((["leechc"], "leechc"), (["leech", "check"], "leech"), (["leech", "build"], "leech")),
)
def test_internal_error_renders_earlier_diagnostics_first(
    tmp_path, monkeypatch, capsys, argv, tool
):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn main() i32 { return true; }\n")
    _crash_after_checking(monkeypatch)
    monkeypatch.setattr(sys, "argv", [*argv, str(src_path)])

    with pytest.raises(RuntimeError, match="boom"):
        if tool == "leechc":
            driver.main()
        else:
            cli.main()

    stderr = capsys.readouterr().err
    user_error = stderr.index("ERROR: Return expression has invalid type")
    ice = stderr.index("ERROR: internal compiler error: RuntimeError: boom")
    note = stderr.index(f"NOTE: this is a bug in {tool}; please report it")
    assert user_error < ice < note


def test_parse_error_in_an_import_rejects_only_the_import(compiler):
    program = harness.TestProgram.from_main(
        "import a;\nfn f() i32 { return a::g(); }\nfn h() i32 { return true; }\n",
        harness.ModSrc("a", "pub fn g() i32 { return 0 }\n"),
    )
    diags = diag.Diags()

    with pytest.raises(errors.InvalidRetTypError):
        compiler.build(program, diags=diags)

    assert [type(d) for d in diags.sorted()] == [
        errors.InvalidRetTypError,
        errors.UnexpectedTokenError,
    ]


def test_rejected_import_does_not_load_its_module(compiler):
    program = harness.TestProgram(
        harness.ModSrc("main", "import a;\nimport x::a;\n"),
        (
            harness.ModSrc("a", "pub fn f() i32 { return 0; }\n"),
            harness.ModSrc("x::a", "pub fn bad() i32 { return true; }\n"),
        ),
    )
    diags = diag.Diags()

    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.build(program, diags=diags)

    assert [type(d) for d in diags.all()] == [errors.DuplicateItemDefnError]


def test_mismatched_branches_are_ordered_by_their_expression(compiler):
    src = """
    pub fn a() i32 {
        if (true) { 1i32 } else { true };
        return 0;
    }
    pub fn b() i32 { return true; }
    """
    diags = diag.Diags()

    with pytest.raises(errors.IfElsTypMismatchError) as exc_info:
        compiler.build(src, diags=diags)

    harness.assert_span_at(exc_info.value.message.span, src, "if (true)")
    assert [type(d) for d in diags.sorted()] == [
        errors.IfElsTypMismatchError,
        errors.InvalidRetTypError,
    ]


def test_rejected_item_keeps_its_name_so_a_redefinition_is_a_duplicate(compiler):
    src = """
    fn f(*self) i32 { return 0; }
    fn f() i32 { return 0; }
    fn g() i32 { return f(); }
    """
    diags = diag.Diags()

    with pytest.raises(errors.SelfParamOutsideImplError):
        compiler.build(src, diags=diags)

    assert [type(d) for d in diags.sorted()] == [
        errors.SelfParamOutsideImplError,
        errors.DuplicateItemDefnError,
    ]
