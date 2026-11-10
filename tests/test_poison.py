# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds
from tests import harness


def _failed_build_diags(compiler: harness.CompilerHarness, src: str) -> tuple[diag.Diag, ...]:
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build(src)
    return exc_info.value.diags


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


def test_a_failed_let_still_binds_its_name(compiler):
    src = """
    fn g() {}
    pub fn main() i32 {
        let a: i32 = true;
        let b: Missing = 1;
        let c = g();
        let _ = 1;
        return a + b + c + _;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.LET_TYPE_MISMATCH,
        diag_kinds.UNKNOWN_NAME,
        diag_kinds.VOID_INITIALIZER,
        diag_kinds.RESERVED_NAME,
    ]


def test_an_unsuffixed_literal_expected_to_be_poison_is_poison(compiler):
    # Defaulting the literal to i32 would report a false overflow.
    src = """
    pub fn main() i32 {
        let mut a = nope;
        a = 3000000000;
        return 0;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]


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
    harness.assert_span_at(found[0].span, src, "x = 2")
