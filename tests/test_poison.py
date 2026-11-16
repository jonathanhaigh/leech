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


@pytest.mark.parametrize("initializer", ["nope < 1", "nope and true", "true or nope", "not nope"])
def test_an_operator_with_a_poisoned_operand_gives_poison(compiler, initializer):
    src = f"""
    pub fn main() i32 {{
        let a = {initializer};
        return a + 1;
    }}
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [diag_kinds.UNKNOWN_NAME]


def test_failed_field_access_indexing_and_dereferencing_give_poison(compiler):
    src = """
    struct S { a: i32 }
    pub fn main() i32 {
        let s = S { a: 1 };
        let x = 1;
        let b: bool = s.b;
        let c: bool = x.a;
        let d: bool = x.[true];
        let e: bool = x.*;
        return 0;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.UNKNOWN_STRUCT_FIELD,
        diag_kinds.NON_STRUCT_FIELD_ACCESS,
        diag_kinds.NON_ARRAY_INDEX,
        diag_kinds.INDEX_TYPE_MISMATCH,
        diag_kinds.NON_POINTER_DEREFERENCE,
    ]


def test_every_control_flow_error_is_reported(compiler):
    src = """
    pub fn main() i32 {
        if (1) {}
        while (2) { continue outer; }
        break;
        return true;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.IF_CONDITION_TYPE_MISMATCH,
        diag_kinds.WHILE_CONDITION_TYPE_MISMATCH,
        diag_kinds.UNKNOWN_LOOP_LABEL,
        diag_kinds.BREAK_OUTSIDE_LOOP,
        diag_kinds.RETURN_TYPE_MISMATCH,
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


def test_a_brace_literal_value_with_nowhere_to_go_is_still_checked(compiler):
    # The unnamed values might be meant for the missing field, so that isn't reported.
    src = """
    struct S { a: i32 }
    pub fn main() i32 {
        let s = S { b: nope, nope };
        let t = i32 { nope };
        return 0;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.UNKNOWN_STRUCT_FIELD,
        diag_kinds.UNKNOWN_NAME,
        diag_kinds.POSITIONAL_VALUE_IN_STRUCT_EXPRESSION,
        diag_kinds.UNKNOWN_NAME,
        diag_kinds.NON_STRUCT_OR_ARRAY_LITERAL,
        diag_kinds.UNKNOWN_NAME,
    ]


def test_every_array_literal_error_is_reported(compiler):
    src = """
    pub fn main() i32 {
        let a = array[i32, 2]{x: 1, true, 3};
        return 0;
    }
    """
    found = _failed_build_diags(compiler, src)

    assert [d.kind for d in found] == [
        diag_kinds.ARRAY_ELEMENT_COUNT_MISMATCH,
        diag_kinds.NAMED_FIELD_IN_ARRAY_LITERAL,
        diag_kinds.ARRAY_ELEMENT_TYPE_MISMATCH,
    ]
