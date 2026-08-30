# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_unexpected_character_message(tmp_path):
    src = """pub fn main() i32 {
    return 0 @ 1;
}
"""
    with pytest.raises(errors.UnexpectedCharacterError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"@"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "@")

    # Unlike UnexpectedTokenError, there's no "expected" note: the lexer
    # couldn't form a token at all, so there's nothing to enumerate.
    assert exc_info.value.extra == []


def test_unexpected_token_message(tmp_path):
    src = """pub fn main() i32 {
    return 0
}
"""
    with pytest.raises(errors.UnexpectedTokenError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"}"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "}")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Expected one of: ";"'
    assert note.span is None


def test_unexpected_end_of_input_message(tmp_path):
    src = """pub fn main() i32 {
    return 0;
"""
    with pytest.raises(errors.UnexpectedTokenError) as exc_info:
        util.compile_str(tmp_path, src)

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
    with pytest.raises(errors.PrivateStructFieldAccessError) as exc_info:
        util.compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"val"' in msg
    assert '"T"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(main_src, "val")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Field "val" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == util.find_pos(a_src, "val")


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
    with pytest.raises(errors.PrivateItemAccessError) as exc_info:
        util.compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"f"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(main_src, "f()")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Function "f" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == util.find_pos(a_src, "fn f()")


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
    with pytest.raises(errors.PrivateItemAccessError) as exc_info:
        util.compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"x"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(main_src, "x")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Variable "x" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == util.find_pos(a_src, "let x")


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
    with pytest.raises(errors.PrivateItemAccessError) as exc_info:
        util.compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"T"' in msg
    assert "private" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(main_src, "T{")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert note.message == 'Type "T" defined here'
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == util.find_pos(a_src, "struct T")


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
    with pytest.raises(errors.VoidVarInitializerError) as exc_info:
        util.compile_str(tmp_path, src)

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
    with pytest.raises(errors.VoidVarInitializerError) as exc_info:
        util.compile_str(tmp_path, src)

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
    with pytest.raises(errors.ModUsedAsTypError) as exc_info:
        util.compile_modules(tmp_path, main=main_src, a=a_src)

    msg = str(exc_info.value)
    assert '"a"' in msg
    assert "cannot be used as a type" in msg

    # The caret points at the path segment naming the module, not at the
    # import that bound it.
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(main_src, "a) i32")

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
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_modules(tmp_path, main=main_src, a=a_src)

    assert str(exc_info.value) == 'Duplicate definition of type or module "a"'


def test_duplicate_generic_fn_message_has_both_declaration_spans(tmp_path):
    src = """fn id[T](x: T) T { x }
fn id[U](x: U) U { x }
pub fn main() i32 { 0 }
"""
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_str(tmp_path, src)

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "fn id[U]")
    (note,) = exc_info.value.extra
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == util.find_pos(src, "fn id[T]")


def test_overlapping_inherent_impl_assoc_fn_name_clash_message(tmp_path):
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
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"get"' in msg
    assert "associated function" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "get(*self) i32 { 1 }")

    (note,) = exc_info.value.extra
    assert note.message == "Previous definition here"
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == util.find_pos(src, "get(*self) i32 { 0 }")


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
    with pytest.raises(errors.InfiniteSizeStructError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"A"' in msg
    assert "infinite size" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "struct A")

    assert len(exc_info.value.extra) == 2
    first, second = exc_info.value.extra
    assert first.message == 'Field "b" of struct "A" contains "B" by value'
    assert first.span is not None
    assert (first.span.start_line, first.span.start_col) == util.find_pos(src, "b: B")
    assert second.message == 'Field "a" of struct "B" contains "A" by value'
    assert second.span is not None
    assert (second.span.start_line, second.span.start_col) == util.find_pos(src, "a: A")


def test_circular_var_initializer_message(tmp_path):
    src = """
    let b = a;
    let a = b;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CircularVarInitializerError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"b"' in msg
    assert "depends on itself" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "let b")

    assert len(exc_info.value.extra) == 2
    first, second = exc_info.value.extra
    assert first.message == 'Variable "b" defined here'
    assert first.span is not None
    assert (first.span.start_line, first.span.start_col) == util.find_pos(src, "let b")
    assert second.message == 'Variable "a" defined here'
    assert second.span is not None
    assert (second.span.start_line, second.span.start_col) == util.find_pos(src, "let a")


def test_recursive_trait_bound_message(tmp_path):
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
    with pytest.raises(errors.RecursiveTraitBoundError) as exc_info:
        util.compile_str(tmp_path, src)

    assert exc_info.value.message.message == 'Trait bound "Y" is part of a recursive bound cycle'
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "Y[T]] { fn x")

    assert [note.message for note in exc_info.value.extra] == [
        'Trait bound "Y" participates in this cycle',
        'Trait bound "Z" participates in this cycle',
        'Trait bound "X" participates in this cycle',
    ]
    expected_spans = [
        util.find_pos(src, text) for text in ("Y[T]] { fn x", "Z[T]] { fn y", "X[T]] { fn z")
    ]
    actual_spans = []
    for note in exc_info.value.extra:
        assert note.span is not None
        actual_spans.append((note.span.start_line, note.span.start_col))
    assert actual_spans == expected_spans


def test_recursive_impl_selection_message(tmp_path):
    src = """
    trait A { fn a(*self) i32; }
    trait B { fn b(*self) i32; }
    impl[T: B] A for T { fn a(*self) i32 { 0 } }
    impl[T: A] B for T { fn b(*self) i32 { 0 } }
    pub fn main() i32 { let x: i32 = 1; return x.a(); }
    """
    with pytest.raises(errors.RecursiveImplSelectionError) as exc_info:
        util.compile_str(tmp_path, src)

    assert (
        exc_info.value.message.message
        == 'Selecting an implementation of trait "A" for type "i32" is recursive'
    )
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "impl[T: B]")

    assert [note.message for note in exc_info.value.extra] == [
        'Implementation "<T as A>" participates in this cycle',
        'Implementation "<T as B>" participates in this cycle',
    ]
    expected_spans = [util.find_pos(src, text) for text in ("impl[T: B]", "impl[T: A]")]
    actual_spans = []
    for note in exc_info.value.extra:
        assert note.span is not None
        actual_spans.append((note.span.start_line, note.span.start_col))
    assert actual_spans == expected_spans


def test_if_cond_not_bool_message(tmp_path):
    src = """pub fn main() i32 {
    return if (1 + 2) {
        1
    } else {
        2
    };
}
"""
    with pytest.raises(errors.IfCondNotBoolError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "bool" in msg
    assert '"i32"' in msg
    # Guards against interpolating the Op object's repr instead of its name.
    assert "binary + operation expression" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "1 + 2")


def test_while_cond_not_bool_message(tmp_path):
    src = """pub fn main() i32 {
    let x = 1;
    while (&x) {
    };
    return 0;
}
"""
    with pytest.raises(errors.WhileCondNotBoolError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "bool" in msg
    assert '"*i32"' in msg
    assert "unary & operation expression" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "&x")


def test_loop_label_not_found_message(tmp_path):
    src = """pub fn main() i32 {
    while (true) {
        break nope;
    };
    return 0;
}
"""
    with pytest.raises(errors.LoopLabelNotFoundError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "nope" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "nope")


def test_not_callable_message(tmp_path):
    src = """pub fn main() i32 {
    let x = 100;
    x();
    return 0;
}
"""
    with pytest.raises(errors.NotCallableError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert 'variable "x"' in msg
    assert '"i32"' in msg
    assert "not callable" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "x();")


def test_invalid_arg_typ_message(tmp_path):
    src = """pub fn f(x: i32) i32 { x + x }
pub fn main() i32 {
    f("abc");
    return 0;
}
"""
    with pytest.raises(errors.InvalidArgTypError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "Argument 1" in msg
    assert "callable" in msg
    assert '"*u8"' in msg
    assert '"i32"' in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, '"abc"')


def test_invalid_bin_op_arg_typ_message(tmp_path):
    src = """pub fn main() i32 {
    let x = true + 1;
    return 0;
}
"""
    with pytest.raises(errors.InvalidBinOpArgTypError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "operand" in msg
    assert '"+"' in msg
    assert '"bool"' in msg
    assert "an integer type" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "true + 1")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert '"+"' in note.message
    assert note.span is not None
    assert (note.span.start_line, note.span.start_col) == util.find_pos(src, "+ 1")


def test_if_els_typ_mismatch_message(tmp_path):
    src = """pub fn main() i32 {
    if (true) { 1 } else { "abc" };
    return 0;
}
"""
    with pytest.raises(errors.IfElsTypMismatchError) as exc_info:
        util.compile_str(tmp_path, src)

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
    assert (then_note.span.start_line, then_note.span.start_col) == util.find_pos(src, "{ 1 }")
    assert '"*u8"' in els_note.message
    assert els_note.span is not None
    assert (els_note.span.start_line, els_note.span.start_col) == util.find_pos(src, '{ "abc" }')


def test_non_exhaustive_match_message(tmp_path):
    src = """
    enum Color { Red, Green, Blue }
    pub fn main() i32 {
        let c = Color::Green;
        return match (c) { Color::Red => 0i32, };
    }
    """
    with pytest.raises(errors.NonExhaustiveMatchError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"match"' in msg
    assert "not exhaustive" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "match (c)")

    assert [note.message for note in exc_info.value.extra] == [
        'Uncovered pattern "Color::Green"',
        'Uncovered pattern "Color::Blue"',
    ]
    assert all(note.span is None for note in exc_info.value.extra)


def test_match_arm_typ_mismatch_message(tmp_path):
    src = """
    pub fn main() i32 {
        return match (true) {
            true => 0i32,
            false => "no",
        };
    }
    """
    with pytest.raises(errors.MatchArmTypMismatchError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"match"' in msg
    assert "mismatching types" in msg
    assert exc_info.value.message.span is None

    assert len(exc_info.value.extra) == 2
    first_note, second_note = exc_info.value.extra
    assert '"i32"' in first_note.message
    assert first_note.span is not None
    assert (first_note.span.start_line, first_note.span.start_col) == util.find_pos(src, "0i32")
    assert '"*u8"' in second_note.message
    assert second_note.span is not None
    assert (second_note.span.start_line, second_note.span.start_col) == util.find_pos(src, '"no"')


def test_missing_typ_args_message(tmp_path):
    src = """fn id[T](x: T) T { return x; }
pub fn main() i32 {
    let f = id;
    return 0;
}
"""
    with pytest.raises(errors.MissingComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"id"' in msg
    assert "without required comptime arguments" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "id;")


def test_cannot_infer_typ_arg_message(tmp_path):
    src = """fn id[T](x: T) T { return x; }
pub fn main() i32 {
    id(5);
    return 0;
}
"""
    with pytest.raises(errors.CannotInferComptimeArgError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"T"' in msg
    assert '"id"' in msg
    assert "Cannot infer argument" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "id(5)")

    assert len(exc_info.value.extra) == 1
    note = exc_info.value.extra[0]
    assert '"id[' in note.message


def test_wrong_number_of_typ_args_message(tmp_path):
    src = """fn id[T](x: T) T { return x; }
pub fn main() i32 {
    id[i32, bool](5);
    return 0;
}
"""
    with pytest.raises(errors.WrongNumberOfComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"id"' in msg
    assert "got 2" in msg
    assert "expected 1" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "id[i32, bool](5)")


def test_typ_args_on_non_generic_item_message(tmp_path):
    src = """fn f(x: i32) i32 { return x; }
pub fn main() i32 {
    f[i32](5);
    return 0;
}
"""
    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert '"f"' in msg
    assert "not generic" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "f[i32](5)")


def test_unconstrained_impl_typ_param_message(tmp_path):
    src = """struct Box[T] { val: T }
impl[T, U] Box[T] {
    fn get(*self) T { return self.*.val; }
}
pub fn main() i32 { return 0; }
"""
    with pytest.raises(errors.UnconstrainedImplComptimeParamError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert 'Impl parameter "U"' in msg
    assert "not constrained by the impl self type" in msg

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "U]")
