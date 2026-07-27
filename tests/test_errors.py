import pytest

from leech.leecherrors import (
    CircularVarInitializerError,
    DuplicateItemDefnError,
    ModUsedAsTypError,
    IfCondNotBoolError,
    IfElsTypMismatchError,
    InfiniteSizeStructError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    LoopLabelNotFoundError,
    NotCallableError,
    PrivateItemAccessError,
    PrivateStructFieldAccessError,
    UnexpectedCharacterError,
    UnexpectedTokenError,
    VoidVarInitializerError,
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


def test_private_fn_access_message(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f();
    }
    """
    a_src = """
    fn f() i32 {
        return 1;
    }
    """
    with pytest.raises(PrivateItemAccessError) as exc_info:
        compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"f"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(main_src, "f()")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Function "f" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == find_pos(a_src, "fn f()")


def test_private_var_access_message(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::x;
    }
    """
    a_src = """
    let x = 11;
    """
    with pytest.raises(PrivateItemAccessError) as exc_info:
        compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"x"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(main_src, "x")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Variable "x" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == find_pos(a_src, "let x")


def test_private_typ_access_message(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        let x = a::T{int: 32};
        return x.int;
    }
    """
    a_src = """
    struct T {
        int: i32,
    }
    """
    with pytest.raises(PrivateItemAccessError) as exc_info:
        compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"T"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(main_src, "T{")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Type "T" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == find_pos(a_src, "struct T")


def test_void_local_var_initializer_message(tmp_path):
    # The message used to say "Module variable initializer cannot be
    # void" even for this local (in-function) let statement, which was
    # actively misleading - it isn't a module variable at all.
    src = """
    fn f() { }
    pub fn main() i32 {
        let x = f();
        return 0;
    }
    """
    with pytest.raises(VoidVarInitializerError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "void" in msg
    assert "Module" not in msg


def test_void_mod_var_initializer_message(tmp_path):
    src = """
    fn f() { }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(VoidVarInitializerError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "void" in msg


def test_mod_used_as_typ_message(tmp_path):
    main_src = """
    import a;
    fn g(p: a) i32 {
        return 0;
    }
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    pub fn f() i32 {
        return 1;
    }
    """
    with pytest.raises(ModUsedAsTypError) as exc_info:
        compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"a"' in msg
    assert "cannot be used as a type" in msg

    # The caret points at the path segment naming the module, not at the
    # import that bound it.
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(main_src, "a) i32")

    # The note has no span of its own - a module's AST node covers its
    # whole file, so there's nothing useful to point at.
    (note,) = exc_info.value.extra
    assert 'e.g. "a::SomeTyp"' in note.message
    assert note.span is None


def test_mod_and_typ_name_clash_message(tmp_path):
    # Modules share the container namespace with types, so the duplicate
    # diagnostic has to name both possibilities rather than just "type".
    main_src = """
    import a;
    struct a { x: i32 }
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    pub fn f() i32 {
        return 1;
    }
    """
    with pytest.raises(DuplicateItemDefnError) as exc_info:
        compile_modules(tmp_path, main=main_src, a=a_src)

    assert str(exc_info.value) == 'Duplicate definition of type or module "a"'


def test_field_and_assoc_fn_name_clash_message(tmp_path):
    # The note pointing at the field is what makes this diagnostic
    # comprehensible: without it, "duplicate associated function" gives no
    # hint that the other definition is a field.
    src = """
    struct Counter {
        get: i32,
    }
    impl Counter {
        fn get(*self) i32 { 0 }
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(DuplicateItemDefnError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"get"' in msg
    assert "associated function" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "get(*self)")

    (note,) = exc_info.value.extra
    assert note.message == "Previous definition here"
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == find_pos(src, "get: i32")


def test_infinite_size_struct_message(tmp_path):
    src = """
    struct A {
        b: B,
    }
    struct B {
        a: A,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(InfiniteSizeStructError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"A"' in msg
    assert "infinite size" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "struct A")

    assert len(exc_info.value.extra) == 2
    first, second = exc_info.value.extra
    assert first.message == 'Field "b" of struct "A" contains "B" by value'
    assert first.span is not None
    assert (first.span.start_line, first.span.start_col) == find_pos(src, "b: B")
    assert second.message == 'Field "a" of struct "B" contains "A" by value'
    assert second.span is not None
    assert (second.span.start_line, second.span.start_col) == find_pos(src, "a: A")


def test_circular_var_initializer_message(tmp_path):
    src = """
    let b = a;
    let a = b;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CircularVarInitializerError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"b"' in msg
    assert "depends on itself" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "let b")

    assert len(exc_info.value.extra) == 2
    first, second = exc_info.value.extra
    assert first.message == 'Variable "b" defined here'
    assert first.span is not None
    assert (first.span.start_line, first.span.start_col) == find_pos(src, "let b")
    assert second.message == 'Variable "a" defined here'
    assert second.span is not None
    assert (second.span.start_line, second.span.start_col) == find_pos(src, "let a")


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


def test_loop_label_not_found_message(tmp_path):
    src = """pub fn main() i32 {
    while (true) {
        break nope;
    };
    return 0;
}
"""
    with pytest.raises(LoopLabelNotFoundError) as exc_info:
        compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "nope" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == find_pos(src, "nope")


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
