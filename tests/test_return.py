# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds
from tests import harness


def test_tail_expr_return(compiler):
    src = """
    pub fn main() i32 { 100 }
    """
    compiler.check(src, exit_status=100)


def test_missing_return(compiler):
    src = """
    extern fn puts(s: *u8) i32;
    pub fn main() i32 {
        puts("abcd");
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.MISSING_RETURN,)


def test_invalid_void_return(compiler):
    src = """
    pub fn main() i32 {
        return;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.MISSING_RETURN_VALUE,)

    err = exc_info.value.diags[0]
    harness.assert_span_at(err.span, src, "return;")
    (label,) = err.labels
    assert label.msg.kind is diag_kinds.RET_TYP_HERE
    harness.assert_span_at(label.span, src, "i32 {")


def test_invalid_return_typ(compiler):
    src = """
    pub fn main() i32 {
        return "abcd";
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.RETURN_TYPE_MISMATCH,)

    err = exc_info.value.diags[0]
    harness.assert_span_at(err.span, src, '"abcd"')
    (label,) = err.labels
    assert label.msg.kind is diag_kinds.RET_TYP_HERE
    harness.assert_span_at(label.span, src, "i32 {")


def test_return_typ_not_defined(compiler):
    src = """
    pub fn f() not_a_typ { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNKNOWN_NAME,)


def test_comptime_return(compiler):
    src = """
    let x = {
        return 1;
    };
    pub fn main() i32 { 0 }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.RETURN_OUTSIDE_FUNCTION,)


def test_unreachable_code_warns_once_per_block_at_its_first_statement(compiler):
    src = """fn g() i32 { return 0; }
pub fn f(b: bool) i32 {
    if (b) {
        return 1;
        g();
        g();
    } else {
        return 2;
        g();
    }
}
"""
    compiled = compiler.compile(src)

    warnings = compiled.diags.all()
    assert [w.kind for w in warnings] == [diag_kinds.UNREACHABLE_CODE] * 2
    harness.assert_span_at(warnings[0].span, src, "g();\n        g();")
    harness.assert_span_at(warnings[1].span, src, "g();\n    }\n}")
