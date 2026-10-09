# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds
from tests import harness


def test_unexpected_character_message(compiler):
    src = """pub fn main() i32 {
    return 0 @ 1;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNEXPECTED_CHARACTER,)

    err = exc_info.value.diags[0]
    assert str(err) == 'unexpected character "@"'
    harness.assert_span_at(err.span, src, "@")

    # Unlike an unexpected token, there's no "expected" note: the lexer
    # couldn't form a token at all, so there's nothing to enumerate.
    assert isinstance(err, diag.Diag)
    assert err.notes == ()


def test_unexpected_token_message(compiler):
    src = """pub fn main() i32 {
    return 0
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNEXPECTED_TOKEN,)

    err = exc_info.value.diags[0]
    assert str(err) == 'unexpected token "}"'
    harness.assert_span_at(err.span, src, "}")

    assert isinstance(err, diag.Diag)
    (note,) = err.notes
    assert note.msg.text() == 'expected one of: ";"'
    assert note.span is None


def test_unexpected_end_of_input_message(compiler):
    src = """pub fn main() i32 {
    return 0;
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNEXPECTED_TOKEN,)

    err = exc_info.value.diags[0]
    assert str(err) == "unexpected end of input"

    span = err.span
    assert span is not None
    # Positioned right after the last real token, since there's nothing
    # left in the source for the span to point at directly.
    assert span.start_line == 2

    assert isinstance(err, diag.Diag)
    (note,) = err.notes
    assert note.span is None
    # Many different statement/expression-starting tokens are valid here;
    # spot-check a representative few rather than the whole list (which
    # is checked exactly in test_cli.py's equivalent scenario).
    note_text = note.msg.text()
    assert '"return"' in note_text
    assert '"}"' in note_text
    assert "IDENT" in note_text


def test_private_struct_field_access_message(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.PRIVATE_FIELD_ACCESS,)

    msg = str(exc_info.value.diags[0])
    assert '"val"' in msg
    assert '"T"' in msg
    assert "private" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "val")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 1
    note = harness.user_error(exc_info.value.diags[0]).extra[0]
    assert note.message == 'Field "val" defined here'
    harness.assert_span_at(note.span, a_src, "val")


def test_private_fn_access_message(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.PRIVATE_ITEM_ACCESS,)

    msg = str(exc_info.value.diags[0])
    assert '"f"' in msg
    assert "private" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "f()")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 1
    note = harness.user_error(exc_info.value.diags[0]).extra[0]
    assert note.message == 'Function "f" defined here'
    harness.assert_span_at(note.span, a_src, "fn f()")


def test_private_var_access_message(compiler):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::x;
    }
    """
    a_src = """
    let x = 11;
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.PRIVATE_ITEM_ACCESS,)

    msg = str(exc_info.value.diags[0])
    assert '"x"' in msg
    assert "private" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "x")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 1
    note = harness.user_error(exc_info.value.diags[0]).extra[0]
    assert note.message == 'Variable "x" defined here'
    harness.assert_span_at(note.span, a_src, "let x")


def test_private_typ_access_message(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.PRIVATE_ITEM_ACCESS,)

    msg = str(exc_info.value.diags[0])
    assert '"T"' in msg
    assert "private" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "T{")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 1
    note = harness.user_error(exc_info.value.diags[0]).extra[0]
    assert note.message == 'Type "T" defined here'
    harness.assert_span_at(note.span, a_src, "struct T")


def test_void_local_var_initializer_message(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.VOID_INITIALIZER,)

    msg = str(exc_info.value.diags[0])
    assert "void" in msg
    assert "Module" not in msg


def test_void_mod_var_initializer_message(compiler):
    src = """
    fn f() { }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.VOID_INITIALIZER,)

    msg = str(exc_info.value.diags[0])
    assert "void" in msg


def test_mod_used_as_typ_message(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.MODULE_USED_AS_TYPE,)

    msg = str(exc_info.value.diags[0])
    assert '"a"' in msg
    assert "cannot be used as a type" in msg

    # The caret points at the path segment naming the module, not at the
    # import that bound it.
    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "a) i32")

    # The note has no span of its own - a module's AST node covers its
    # whole file, so there's nothing useful to point at.
    (note,) = harness.user_error(exc_info.value.diags[0]).extra
    assert 'e.g. "a::SomeTyp"' in note.message
    assert note.span is None


def test_mod_and_typ_name_clash_message(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert exc_info.value.kinds == (diag_kinds.DUPLICATE_DEFINITION,)

    assert str(exc_info.value.diags[0]) == 'Duplicate definition of type or module "a"'


def test_conflicting_extern_decl_message(compiler):
    src = "extern fn write(fd: i32, buf: *u8, count: usize) i32;\npub fn main() i32 { 0 }"

    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_EXTERN_DECLARATIONS,)

    assert str(exc_info.value.diags[0]) == (
        'Extern function "write" is declared with type "fn(i32, *u8, u64) i32", but was '
        'declared with type "fn(i32, *u8, u64) i64"'
    )
    (note,) = harness.user_error(exc_info.value.diags[0]).extra
    assert note.message == "Earlier declaration here"


def test_duplicate_generic_fn_message_has_both_declaration_spans(compiler):
    src = """fn id[T](x: T) T { x }
fn id[U](x: U) U { x }
pub fn main() i32 { 0 }
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.DUPLICATE_DEFINITION,)

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "fn id[U]")
    (note,) = harness.user_error(exc_info.value.diags[0]).extra
    harness.assert_span_at(note.span, src, "fn id[T]")


def test_overlapping_inherent_impl_assoc_fn_name_clash_message(compiler):
    src = """
    struct Counter {}
    impl Counter {
        fn get(*self) i32 { 0 }
    }
    impl Counter {
        fn get(*self) i32 { 1 }
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.DUPLICATE_DEFINITION,)

    msg = str(exc_info.value.diags[0])
    assert '"get"' in msg
    assert "associated function" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "get(*self) i32 { 1 }")

    (note,) = harness.user_error(exc_info.value.diags[0]).extra
    assert note.message == "Previous definition here"
    harness.assert_span_at(note.span, src, "get(*self) i32 { 0 }")


def test_infinite_size_struct_message(compiler):
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
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,)

    msg = str(exc_info.value.diags[0])
    assert '"A"' in msg
    assert "infinite size" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "struct A")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 2
    first, second = harness.user_error(exc_info.value.diags[0]).extra
    assert first.message == 'Field "b" of struct "A" contains "B" by value'
    harness.assert_span_at(first.span, src, "b: B")
    assert second.message == 'Field "a" of struct "B" contains "A" by value'
    harness.assert_span_at(second.span, src, "a: A")


def test_wrong_number_of_payload_patterns_message(compiler):
    src = """
    enum Color { Red, Green }
    pub fn main() i32 {
        let c = Color::Red;
        return match (c) {
            Color::Red(let x) => x,
            _ => 1i32,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,)

    assert harness.user_error(exc_info.value.diags[0]).message.message == (
        'Wrong number of payload patterns for variant "Color::Red": got 1, expected 0'
    )
    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "Color::Red(let x)")


def test_circular_var_initializer_message(compiler):
    src = """
    let b = a;
    let a = b;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.RECURSIVE_INITIALIZER,)

    msg = str(exc_info.value.diags[0])
    assert '"b"' in msg
    assert "depends on itself" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "let b")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 2
    first, second = harness.user_error(exc_info.value.diags[0]).extra
    assert first.message == 'Variable "b" defined here'
    harness.assert_span_at(first.span, src, "let b")
    assert second.message == 'Variable "a" defined here'
    harness.assert_span_at(second.span, src, "let a")


def test_recursive_trait_bound_message(compiler):
    src = """
    trait W[T: X[T]] { fn w(*self) T; }
    trait X[T: Y[T]] { fn x(*self) T; }
    trait Y[T: Z[T]] { fn y(*self) T; }
    trait Z[T: X[T]] { fn z(*self) T; }
    fn f[U: W[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.RECURSIVE_TRAIT_BOUND,)

    assert (
        harness.user_error(exc_info.value.diags[0]).message.message
        == 'Trait bound "Y[T]" is part of a recursive bound cycle'
    )
    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "Y[T]] { fn x")

    assert [note.message for note in harness.user_error(exc_info.value.diags[0]).extra] == [
        'Trait bound "Y[T]" participates in this cycle',
        'Trait bound "Z[T]" participates in this cycle',
        'Trait bound "X[T]" participates in this cycle',
    ]
    expected_spans = [
        harness.src_position(src, text) for text in ("Y[T]] { fn x", "Z[T]] { fn y", "X[T]] { fn z")
    ]
    actual_spans = []
    for note in harness.user_error(exc_info.value.diags[0]).extra:
        assert note.span is not None
        actual_spans.append((note.span.start_line, note.span.start_col))
    assert actual_spans == expected_spans


def test_recursive_impl_selection_message(compiler):
    src = """
    trait A { fn a(*self) i32; }
    trait B { fn b(*self) i32; }
    impl[T: B] A for T { fn a(*self) i32 { 0 } }
    impl[T: A] B for T { fn b(*self) i32 { 0 } }
    pub fn main() i32 { let x: i32 = 1; return x.a(); }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.RECURSIVE_IMPL_SELECTION,)

    assert (
        harness.user_error(exc_info.value.diags[0]).message.message
        == 'Selecting an implementation of trait "A" for type "i32" is recursive'
    )
    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "impl[T: B]")

    assert [note.message for note in harness.user_error(exc_info.value.diags[0]).extra] == [
        'Implementation "<T as A>" participates in this cycle',
        'Implementation "<T as B>" participates in this cycle',
    ]
    expected_spans = [harness.src_position(src, text) for text in ("impl[T: B]", "impl[T: A]")]
    actual_spans = []
    for note in harness.user_error(exc_info.value.diags[0]).extra:
        assert note.span is not None
        actual_spans.append((note.span.start_line, note.span.start_col))
    assert actual_spans == expected_spans


def test_if_cond_not_bool_message(compiler):
    src = """pub fn main() i32 {
    return if (1 + 2) {
        1
    } else {
        2
    };
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.IF_CONDITION_TYPE_MISMATCH,)

    msg = str(exc_info.value.diags[0])
    assert "bool" in msg
    assert '"i32"' in msg
    # Guards against interpolating the Op object's repr instead of its name.
    assert "binary + operation expression" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "1 + 2")


def test_while_cond_not_bool_message(compiler):
    src = """pub fn main() i32 {
    let x = 1;
    while (&x) {
    };
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.WHILE_CONDITION_TYPE_MISMATCH,)

    msg = str(exc_info.value.diags[0])
    assert "bool" in msg
    assert '"*i32"' in msg
    assert "unary & operation expression" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "&x")


def test_loop_label_not_found_message(compiler):
    src = """pub fn main() i32 {
    while (true) {
        break nope;
    };
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNKNOWN_LOOP_LABEL,)

    msg = str(exc_info.value.diags[0])
    assert "nope" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "nope")


def test_not_callable_message(compiler):
    src = """pub fn main() i32 {
    let x = 100;
    x();
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.NON_FUNCTION_CALL,)

    msg = str(exc_info.value.diags[0])
    assert 'variable "x"' in msg
    assert '"i32"' in msg
    assert "not callable" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "x();")


def test_invalid_arg_typ_message(compiler):
    src = """pub fn f(x: i32) i32 { x + x }
pub fn main() i32 {
    f("abc");
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.ARGUMENT_TYPE_MISMATCH,)

    msg = str(exc_info.value.diags[0])
    assert "Argument 1" in msg
    assert "callable" in msg
    assert '"*u8"' in msg
    assert '"i32"' in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, '"abc"')


def test_invalid_bin_op_arg_typ_message(compiler):
    src = """pub fn main() i32 {
    let x = true + 1;
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.BINARY_OPERAND_TYPE_MISMATCH,)

    msg = str(exc_info.value.diags[0])
    assert "operand" in msg
    assert '"+"' in msg
    assert '"bool"' in msg
    assert "an integer type" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "true + 1")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 1
    note = harness.user_error(exc_info.value.diags[0]).extra[0]
    assert '"+"' in note.message
    harness.assert_span_at(note.span, src, "+ 1")


def test_if_els_typ_mismatch_message(compiler):
    src = """pub fn main() i32 {
    if (true) { 1 } else { "abc" };
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_BRANCH_TYPES,)

    msg = str(exc_info.value.diags[0])
    assert '"if"' in msg
    assert '"else"' in msg
    assert "mismatching types" in msg
    # Guards against a stray/missing quote in the hand-formatted message.
    assert '""' not in msg

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 2
    then_note, els_note = harness.user_error(exc_info.value.diags[0]).extra
    assert '"i32"' in then_note.message
    harness.assert_span_at(then_note.span, src, "{ 1 }")
    assert '"*u8"' in els_note.message
    harness.assert_span_at(els_note.span, src, '{ "abc" }')


def test_non_exhaustive_match_message(compiler):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Green;
        return match (c) { Color::Red => 0i32, };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.NON_EXHAUSTIVE_MATCH,)

    msg = str(exc_info.value.diags[0])
    assert '"match"' in msg
    assert "not exhaustive" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "match (c)")

    assert [note.message for note in harness.user_error(exc_info.value.diags[0]).extra] == [
        'Uncovered pattern "Color::Green"',
        'Uncovered pattern "Color::Blue"',
    ]
    assert all(note.span is None for note in harness.user_error(exc_info.value.diags[0]).extra)


def test_match_arm_typ_mismatch_message(compiler):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => 0i32,
            false => "no",
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_MATCH_ARM_TYPES,)

    msg = str(exc_info.value.diags[0])
    assert '"match"' in msg
    assert "mismatching types" in msg
    harness.assert_span_at(exc_info.value.diags[0].span, src, "match (true)")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 2
    first_note, second_note = harness.user_error(exc_info.value.diags[0]).extra
    assert '"i32"' in first_note.message
    harness.assert_span_at(first_note.span, src, "0i32")
    assert '"*u8"' in second_note.message
    harness.assert_span_at(second_note.span, src, '"no"')


def test_missing_typ_args_message(compiler):
    src = """fn id[T](x: T) T { return x; }
pub fn main() i32 {
    let f = id;
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.MISSING_COMPTIME_ARGUMENT,)

    msg = str(exc_info.value.diags[0])
    assert '"id"' in msg
    assert "without required comptime arguments" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "id;")


def test_cannot_infer_typ_arg_message(compiler):
    src = """fn id[T](x: T) T { return x; }
pub fn main() i32 {
    id(5);
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNINFERABLE_COMPTIME_ARGUMENT,)

    msg = str(exc_info.value.diags[0])
    assert '"T"' in msg
    assert '"id"' in msg
    assert "Cannot infer argument" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "id(5)")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 1
    note = harness.user_error(exc_info.value.diags[0]).extra[0]
    assert '"id[' in note.message


def test_wrong_number_of_typ_args_message(compiler):
    src = """fn id[T](x: T) T { return x; }
pub fn main() i32 {
    id[i32, bool](5);
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.COMPTIME_ARGUMENT_COUNT_MISMATCH,)

    msg = str(exc_info.value.diags[0])
    assert '"id"' in msg
    assert "got 2" in msg
    assert "expected 1" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "id[i32, bool](5)")


def test_typ_args_on_non_generic_item_message(compiler):
    src = """fn f(x: i32) i32 { return x; }
pub fn main() i32 {
    f[i32](5);
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNEXPECTED_COMPTIME_ARGUMENT,)

    msg = str(exc_info.value.diags[0])
    assert '"f"' in msg
    assert "not generic" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "f[i32](5)")


@pytest.mark.parametrize(
    ("src", "qualifier", "item_kind", "item_name"),
    [
        ("pub fn main() i32 { return i32::x; }", "i32::x", "Type", "i32"),
        (
            "pub fn main() i32 { return array[i32, 3]::x; }",
            "array[i32, 3]::x",
            "Type",
            "array[i32, 3]",
        ),
        (
            "trait Show { fn show(*self) i32; } pub fn main() i32 { return Show::x; }",
            "Show::x",
            "Trait",
            "Show",
        ),
        (
            "fn f[T]() i32 { return T::x; } pub fn main() i32 { return 0; }",
            "T::x",
            "Type parameter",
            "T",
        ),
        (
            "fn f[value N: usize]() i32 { return N::x; } pub fn main() i32 { return 0; }",
            "N::x",
            "Value",
            "N",
        ),
    ],
)
def test_non_scope_item_cannot_qualify_path(compiler, src, qualifier, item_kind, item_name):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PATH_QUALIFIER_KIND_MISMATCH,)

    assert str(exc_info.value.diags[0]) == f'{item_kind} "{item_name}" cannot qualify a path'
    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, qualifier)


def test_unconstrained_impl_typ_param_message(compiler):
    src = """struct Box[T] { val: T }
impl[T, U] Box[T] {
    fn get(*self) T { return self.*.val; }
}
pub fn main() i32 { return 0; }
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNCONSTRAINED_IMPL_PARAMETER,)

    msg = str(exc_info.value.diags[0])
    assert 'Impl parameter "U"' in msg
    assert "not constrained by the impl self type" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "U]")


def test_non_exhaustive_match_over_a_union_message(compiler):
    src = """
    union Option[T] { None, Some(T) }
    pub fn main() i32 {
        let o = Option::Some(1i32);
        return match (o) { Option::None => 0i32, };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.NON_EXHAUSTIVE_MATCH,)

    msg = str(exc_info.value.diags[0])
    assert '"match"' in msg
    assert "not exhaustive" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "match (o)")

    # The witness carries a payload column, which a wildcard stands for.
    assert [note.message for note in harness.user_error(exc_info.value.diags[0]).extra] == [
        'Uncovered pattern "Option::Some(_)"',
    ]
    assert all(note.span is None for note in harness.user_error(exc_info.value.diags[0]).extra)


def test_wrong_number_of_payload_patterns_over_a_union_message(compiler):
    src = """
    union Pair { Both(i32, i32) }
    pub fn main() i32 {
        let p = Pair::Both(1i32, 2i32);
        return match (p) {
            Pair::Both(let x) => x,
        };
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PAYLOAD_PATTERN_COUNT_MISMATCH,)

    assert harness.user_error(exc_info.value.diags[0]).message.message == (
        'Wrong number of payload patterns for variant "Pair::Both": got 1, expected 2'
    )
    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "Pair::Both(let x)")


def test_infinite_size_union_message(compiler):
    src = """
    union Tree {
        Leaf,
        Node(i32, Tree),
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.INFINITELY_SIZED_TYPE,)

    msg = str(exc_info.value.diags[0])
    assert '"Tree"' in msg
    assert "infinite size" in msg

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "union Tree")

    assert len(harness.user_error(exc_info.value.diags[0]).extra) == 1
    (hop,) = harness.user_error(exc_info.value.diags[0]).extra
    assert hop.message == 'Payload 1 of variant "Node" of union "Tree" contains "Tree" by value'
    harness.assert_span_at(hop.span, src, "Tree),")
