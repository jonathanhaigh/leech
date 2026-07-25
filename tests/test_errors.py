import pytest

from l0.l0errors import (
    IfCondNotBoolError,
    IfElsTypMismatchError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    NotCallableError,
    WhileCondNotBoolError,
)
from util import compile_str, find_pos


def test_if_cond_not_bool_message(tmp_path):
    src = """pub fn main() i32 {
    return if (1 + 2) {
        1
    } else {
        2
    };
}
"""
    with pytest.raises(IfCondNotBoolError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "bool" in msg
    assert '"i32"' in msg
    # Guards against interpolating the Op object's repr instead of its name.
    assert "binary + operation expression" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "1 + 2")


def test_while_cond_not_bool_message(tmp_path):
    src = """pub fn main() i32 {
    let x = 1;
    while (&x) {
    };
    return 0;
}
"""
    with pytest.raises(WhileCondNotBoolError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "bool" in msg
    assert '"*i32"' in msg
    assert "unary & operation expression" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "&x")


def test_not_callable_message(tmp_path):
    src = """pub fn main() i32 {
    let x = 100;
    x();
    return 0;
}
"""
    with pytest.raises(NotCallableError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert 'variable "x"' in msg
    assert '"i32"' in msg
    assert "not callable" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "x();")


def test_invalid_arg_typ_message(tmp_path):
    src = """pub fn f(x: i32) i32 { x + x }
pub fn main() i32 {
    f("abc");
    return 0;
}
"""
    with pytest.raises(InvalidArgTypError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "Argument 1" in msg
    assert "callable" in msg
    assert '"*u8"' in msg
    assert '"i32"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, '"abc"')


def test_invalid_bin_op_arg_typ_message(tmp_path):
    src = """pub fn main() i32 {
    let x = true + 1;
    return 0;
}
"""
    with pytest.raises(InvalidBinOpArgTypError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "operand" in msg
    assert '"+"' in msg
    assert '"bool"' in msg
    assert "an integer type" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "true + 1")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert '"+"' in note.message
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == find_pos(src, "+ 1")


def test_if_els_typ_mismatch_message(tmp_path):
    src = """pub fn main() i32 {
    if (true) { 1 } else { "abc" };
    return 0;
}
"""
    with pytest.raises(IfElsTypMismatchError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"if"' in msg
    assert '"else"' in msg
    assert "mismatching types" in msg
    # Guards against a stray/missing quote in the hand-formatted message.
    assert '""' not in msg

    assert len(exc_info.value.extra) == 2
    then_note, els_note = exc_info.value.extra
    assert '"i32"' in then_note.message
    assert then_note.span is not None
    assert (then_note.span.start_line, then_note.span.start_col) == find_pos(
        src, "{ 1 }"
    )
    assert '"*u8"' in els_note.message
    assert els_note.span is not None
    assert (els_note.span.start_line, els_note.span.start_col) == find_pos(
        src, '{ "abc" }'
    )
