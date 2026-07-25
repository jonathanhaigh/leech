import pytest

from l0.l0errors import (
    IfCondNotBoolError,
    IfElsTypMismatchError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    NotCallableError,
    PrivateStructFieldAccessError,
    UnexpectedCharacterError,
    UnexpectedTokenError,
    WhileCondNotBoolError,
)
from util import compile_modules, compile_str, find_pos


def test_unexpected_character_message(tmp_path):
    src = """pub fn main() i32 {
    return 0 @ 1;
}
"""
    with pytest.raises(UnexpectedCharacterError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"@"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "@")

    # Unlike UnexpectedTokenError, there's no "expected" note: the lexer
    # couldn't form a token at all, so there's nothing to enumerate.
    assert exc_info.value.extra == []


def test_unexpected_token_message(tmp_path):
    src = """pub fn main() i32 {
    return 0
}
"""
    with pytest.raises(UnexpectedTokenError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"}"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "}")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Expected one of: ";"'
    assert note.span is None


def test_unexpected_end_of_input_message(tmp_path):
    src = """pub fn main() i32 {
    return 0;
"""
    with pytest.raises(UnexpectedTokenError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "Unexpected end of input" in msg

    span = exc_info.value.message.span
    assert span is not None
    # Positioned right after the last real token, since there's nothing
    # left in the source for the span to point at directly.
    assert span.start_line == 2

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.span is None
    # Many different statement/expression-starting tokens are valid here;
    # spot-check a representative few rather than the whole list (which
    # is checked exactly in test_cli.py's equivalent scenario).
    assert '"return"' in note.message
    assert '"}"' in note.message
    assert "IDENT" in note.message


def test_private_struct_field_access_message(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        let t = a::make();
        return t.val;
    }
    """
    a_src = """
    pub struct T {
        val: i32,
    }

    pub fn make() T {
        return T { val: 1 };
    }
    """
    with pytest.raises(PrivateStructFieldAccessError) as exc_info:
        compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"val"' in msg
    assert '"T"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(main_src, "val")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Field "val" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == find_pos(a_src, "val")


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
