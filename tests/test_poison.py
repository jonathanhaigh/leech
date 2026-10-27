# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

from collections.abc import Iterator

import pytest

from leech import ast, diag, diag_kinds, ir_env, ir_module, typs
from tests import harness


def _failed_build_diags(compiler: harness.CompilerHarness, src: str) -> tuple[diag.Diag, ...]:
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build(src)
    return exc_info.value.diags


def _build_diags(compiler: harness.CompilerHarness, src: str) -> tuple[diag.Diag, ...]:
    """Build ``src``, returning its diagnostics if it fails, and none otherwise."""
    try:
        compiler.build(src)
    except diag.CompilationError as err:
        return err.diags
    return ()


def test_two_undefined_variables_report_two_errors(compiler):
    src = """
    pub fn main() i32 {
        let x = a + 1;
        let y = b + 2;
        return x + y;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME, diag_kinds.UNKNOWN_NAME]
    harness.assert_span_at(found[0].span, src, "a + 1")
    harness.assert_span_at(found[1].span, src, "b + 2")


def test_nothing_more_is_reported_about_a_poisoned_variable(compiler):
    src = """
    pub fn main() i32 {
        let a = nope;
        let c: i32 = a + true;
        a = 5;
        return c + a.field;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]
    harness.assert_span_at(found[0].span, src, "nope")


@pytest.mark.parametrize("initializer", ["nope and true", "true or nope", "not nope"])
def test_a_logical_operator_on_poison_gives_poison(compiler, initializer):
    src = f"""
    pub fn main() i32 {{
        let a = {initializer};
        return a + 1;
    }}
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]


def test_a_callee_that_fails_to_resolve_still_has_its_arguments_checked(compiler):
    src = """
    trait A { fn f(*self, y: i32) i32; }
    trait B { fn f(*self, y: i32) i32; }
    impl A for i32 { fn f(*self, y: i32) i32 { return y; } }
    impl B for i32 { fn f(*self, y: i32) i32 { return y; } }
    pub fn main() i32 {
        let x = 1;
        return x.f(nope);
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.AMBIGUOUS_METHOD_CALL,
        diag_kinds.UNKNOWN_NAME,
    ]


def test_a_struct_literal_whose_fields_fail_still_has_its_values_checked(compiler):
    src = """
    struct S { s: S }
    pub fn main() i32 {
        let s = S { s: nope };
        return 0;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.INFINITELY_SIZED_TYPE,
        diag_kinds.UNKNOWN_NAME,
    ]


def test_two_bad_call_arguments_report_two_errors(compiler):
    src = """
    fn f(x: i32, y: bool) i32 { return x; }
    pub fn main() i32 { return f(true, 5); }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.ARGUMENT_TYPE_MISMATCH,
        diag_kinds.ARGUMENT_TYPE_MISMATCH,
    ]
    harness.assert_span_at(found[0].span, src, "true, 5")
    harness.assert_span_at(found[1].span, src, "5)")


def test_a_call_with_the_wrong_arity_still_checks_every_argument(compiler):
    src = """
    fn f(x: i32) i32 { return x; }
    pub fn main() i32 { return f(true, nope); }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.ARGUMENT_TYPE_MISMATCH,
        diag_kinds.ARGUMENT_COUNT_MISMATCH,
        diag_kinds.UNKNOWN_NAME,
    ]


def test_two_bad_struct_literal_fields_report_two_errors(compiler):
    src = """
    struct S { a: i32, b: bool }
    pub fn main() i32 {
        let s = S { a: false, b: 3 };
        return s.a;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.STRUCT_FIELD_TYPE_MISMATCH,
        diag_kinds.STRUCT_FIELD_TYPE_MISMATCH,
    ]
    harness.assert_span_at(found[0].span, src, "false")
    harness.assert_span_at(found[1].span, src, "3 }")


def test_a_poisoned_match_scrutinee_skips_exhaustiveness_and_reachability(compiler):
    src = """
    pub fn main() i32 {
        let a = match (nope) { 1 => 2 };
        let b = match (nope) { _ => 1, _ => 2 };
        return match (nope) { Missing::A(let x) => x, _ => 0 };
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME] * 4
    assert ["Missing" in str(d) for d in found] == [False, False, False, True]


def test_a_failed_pattern_still_binds_its_names(compiler):
    src = """
    union U { A(i32), B }
    pub fn main() i32 {
        return match (U::B) { U::A(let x, let y) => x + y, Nope(let z) => z, _ => 0 };
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,
        diag_kinds.UNKNOWN_NAME,
    ]


def test_an_unknown_parameter_type_is_reported_once(compiler):
    src = """
    fn g(p: Unknown) i32 { return p + 1; }
    pub fn main() i32 { return g(5) + g(true); }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]
    harness.assert_span_at(found[0].span, src, "Unknown")


def test_an_unknown_return_type_is_reported_once(compiler):
    src = """
    fn id[T](x: T) T { return x; }
    fn h() Missing { return 1; }
    pub fn main() i32 {
        let a = id(h());
        return h() + a;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]
    harness.assert_span_at(found[0].span, src, "Missing")


def test_a_poisoned_argument_gives_the_others_no_wrong_expected_type(compiler):
    # Without the first argument, the second would be checked as an i32, and overflow.
    src = """
    fn pick[T](a: T, b: T) T { return a; }
    pub fn main() i32 {
        let x = pick(nope, 3000000000 + 0);
        return 0;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]


def test_poisoned_comptime_arguments_are_not_bound_checked(compiler):
    src = """
    trait Show { fn show(*self) i32; }
    fn shown[T: Show](x: T) i32 { return x.show(); }
    pub fn main() i32 { return shown[Nope](1) + shown(nope); }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME, diag_kinds.UNKNOWN_NAME]
    harness.assert_span_at(found[0].span, src, "Nope")
    harness.assert_span_at(found[1].span, src, "nope")


def test_a_struct_with_a_poisoned_field_stays_usable(compiler):
    src = """
    struct S { a: i32, b: Missing }
    pub fn main() i32 {
        let s = S { a: 1, b: 2 };
        return s.a + s.b;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]


@pytest.mark.parametrize(
    ("initializer", "missing"),
    [
        ("__size_of[S]()", "MissingField"),
        ("__size_of[*S]()", "MissingField"),
        ("match (U::A) { U::A => 1usize, U::B(_) => 2usize }", "MissingPayload"),
    ],
)
def test_a_body_reaching_a_poisoned_member_fails_with_its_proof(compiler, initializer, missing):
    mod = compiler.load(
        f"""
        struct S {{ a: i32, b: MissingField }}
        union U {{ A, B(MissingPayload) }}
        fn f() bool {{ return 1; }}
        let N: usize = {initializer};
        """
    )
    f = mod.get_item(ir_env.Env.Namespace.VARS, "f")
    n = mod.get_item(ir_env.Env.Namespace.VARS, "N")
    assert f is not None and n is not None
    assert isinstance(f.value, ir_module.SrcFnSymbol)
    assert isinstance(n.value, ir_module.ModVar)
    with pytest.raises(diag.ReportedError):
        f.value.check()

    with pytest.raises(diag.ReportedError) as exc_info:
        _ = n.value.initializer

    reported = exc_info.value.reported.diag
    assert reported.kind is diag_kinds.UNKNOWN_NAME
    assert missing in str(reported)


def test_a_body_reaching_poison_through_a_pointer_is_not_lowered(compiler):
    src = """
    struct L { next: *L, item: Missing }
    pub fn f(p: *L) i32 { return 1; return 2; }
    pub fn main() i32 { return 0; }
    """
    found = _failed_build_diags(compiler, src)

    # Lowering f would also have warned about the unreachable return.
    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]


def test_a_statement_that_raises_is_skipped(compiler):
    src = """
    pub fn main() i32 {
        let x = 1;
        let x = 2;
        let y: bool = 3;
        return x;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.DUPLICATE_DEFINITION,
        diag_kinds.LET_TYPE_MISMATCH,
    ]


def test_every_poison_type_carries_a_recorded_error(compiler, monkeypatch):
    # Poison is interned, so its initializer runs once per proof.
    created: list[diag.ReportProof] = []
    init = typs.PoisonTyp.__init__

    def recording_init(self: typs.PoisonTyp, reported: diag.ReportProof) -> None:
        created.append(reported)
        init(self, reported)

    monkeypatch.setattr(typs.PoisonTyp, "__init__", recording_init)
    src = """
    struct S { a: Missing }
    fn id[T](x: T) T { return x; }
    fn pick[T](a: T, b: T) T { return a; }
    pub fn main() i32 {
        let a = id(S { a: 1 });
        let b = pick(id(nope), 3000000000 + 0);
        let c = id(id(5i64 + true));
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build(src)

    assert created
    recorded = exc_info.value.diags
    assert all(any(reported.diag is d for d in recorded) for reported in created)


# Valid programs for the poison-injection test, between them using every kind of
# expression.
_CORPUS = (
    """
struct Point { mut x: i32, mut y: i32 }

impl Point {
    fn sum(*self) i32 { return self.*.x + self.*.y; }
    fn origin() Point { return Point { x: 0, y: 0 }; }
}

union Shape { Circle(i32), Rect(i32, i32), Empty }

enum Color { Red, Green }

let LIMIT: i32 = 3 * 4;

fn area(s: Shape) i32 {
    return match (s) {
        Shape::Circle(let r) => r * r * 3,
        Shape::Rect(let w, let h) => w * h,
        Shape::Empty => 0,
    };
}

fn shade(c: Color) bool {
    return match (c) { Color::Red => true, Color::Green => false };
}

pub fn main() i32 {
    let mut p = Point { x: 1, y: 2 };
    p.x = p.y + LIMIT;
    let q = &p;
    let total = q.*.sum() + Point::origin().sum();
    let mut values = array[i32, 3] { 1, 2, 3 };
    values.[0usize] = values.[1usize] - -2;
    let mut i = 0;
    outer: while (i < 10) {
        i = i + 1;
        if (i == 2 and not shade(Color::Green)) { continue outer; }
        if (i > 5 or total < 0) { break; }
    }
    let label = if (shade(Color::Red)) { area(Shape::Rect(2, 3)) } else { area(Shape::Empty) };
    return match (i) { 1 => label, _ => total + values.[2usize] };
}
""",
    """
trait Show { fn show(*self) i32; }

struct Box[T] { item: T }

impl[T] Box[T] {
    fn get(*self) T { return self.*.item; }
}

impl Show for i32 {
    fn show(*self) i32 { return self.*; }
}

union Maybe[T] { Some(T), None }

fn id[T](x: T) T { return x; }

fn pick[T](a: T, b: T, first: bool) T { if (first) { return a; } return b; }

fn shown[T: Show](x: T) i32 { return x.show(); }

fn len[value N: usize](a: array[i64, N]) usize { return N; }

fn unwrap_or[T](m: Maybe[T], fallback: T) T {
    return match (m) { Maybe::Some(let v) => v, Maybe::None => fallback };
}

pub fn main() i32 {
    let b = Box[i64] { item: id(7i64) };
    let c = Box[i32] { item: 5 };
    let n = pick(b.get(), 3000000000 + 0, true);
    let m = unwrap_or(Maybe::Some(n), 1i64);
    let k = len(array[i64, 2] { m, 2 });
    let e: Maybe[bool] = Maybe::None;
    return shown(c.get()) + id[i32](1);
}
""",
)

_POISON = "__poison__"


def _injection_sites(node: ast.Ast) -> Iterator[ast.Expr]:
    """Yield every expression in ``node``, outside patterns, paths and types."""
    if isinstance(node, ast.Pattern | ast.Path | ast.BasicTyp | ast.PtrTyp):
        return
    if isinstance(node, ast.Expr) and not isinstance(node, ast.StructFieldExpr):
        yield node
    for child in vars(node).values():
        children = child if isinstance(child, list | tuple) else (child,)
        for grandchild in children:
            if isinstance(grandchild, ast.Ast):
                yield from _injection_sites(grandchild)


def _injected_src(expr: ast.Expr) -> str:
    # A block-like expression can be a statement without a semicolon, as a block can.
    if isinstance(expr, ast.BlockExpr | ast.IfExpr | ast.WhileExpr | ast.MatchExpr):
        return f"{{ {_POISON} }}"
    return _POISON


@pytest.mark.parametrize("corpus_index", range(len(_CORPUS)))
def test_replacing_any_expression_with_an_undefined_name_reports_only_that(compiler, corpus_index):
    src = _CORPUS[corpus_index]
    compiler.build(src)
    exprs = list(_injection_sites(compiler.parse(src)))
    assert len(exprs) > 50

    failures = []
    for expr in exprs:
        if isinstance(expr, ast.WhileExpr) and expr.label is not None:
            # Replacing a labelled loop would leave its label undefined.
            continue
        injected = src[: expr.span.start] + _injected_src(expr) + src[expr.span.end :]
        found = _build_diags(compiler, injected)
        if [d.kind for d in found] != [diag_kinds.UNKNOWN_NAME] or _POISON not in str(found[0]):
            failures.append((src[expr.span.start : expr.span.end], [str(d) for d in found]))
    assert failures == []
