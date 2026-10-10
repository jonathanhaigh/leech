# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds, typs
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
    assert '"@"' in str(err)
    harness.assert_span_at(err.span, src, "@")

    # Unlike an unexpected token, there's no "expected" note: the lexer
    # couldn't form a token at all, so there's nothing to enumerate.
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
    assert '"}"' in str(err)
    harness.assert_span_at(err.span, src, "}")

    (note,) = err.notes
    assert note.msg.kind is diag_kinds.EXPECTED_ONE_OF
    assert '";"' in note.msg.text()
    assert note.span is None


def test_unexpected_end_of_input_message(compiler):
    src = """pub fn main() i32 {
    return 0;
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNEXPECTED_TOKEN,)

    err = exc_info.value.diags[0]
    assert "end of input" in str(err)

    span = err.span
    assert span is not None
    # Positioned right after the last real token, since there's nothing
    # left in the source for the span to point at directly.
    assert span.start_line == 2

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

    err = exc_info.value.diags[0]
    assert err.msg.args["field"] == "val"
    assert '"T"' in str(err)
    harness.assert_span_at(err.span, main_src, "val")

    (label,) = err.labels
    assert label.msg.kind is diag_kinds.DEFINED_HERE
    harness.assert_span_at(label.span, a_src, "val")


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

    err = exc_info.value.diags[0]
    assert '"f"' in str(err)

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "f()")

    (label,) = err.labels
    assert label.msg.kind is diag_kinds.DEFINED_HERE
    harness.assert_span_at(label.span, a_src, "fn f()")


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

    err = exc_info.value.diags[0]
    assert '"x"' in str(err)

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "x")

    (label,) = err.labels
    assert label.msg.kind is diag_kinds.DEFINED_HERE
    harness.assert_span_at(label.span, a_src, "let x")


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

    err = exc_info.value.diags[0]
    assert '"T"' in str(err)

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "T{")

    (label,) = err.labels
    assert label.msg.kind is diag_kinds.DEFINED_HERE
    harness.assert_span_at(label.span, a_src, "struct T")


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

    err = exc_info.value.diags[0]
    assert '"a"' in str(err)

    # The caret points at the path segment naming the module, not at the
    # import that bound it.
    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, main_src, "a) i32")

    # The note has no span of its own - a module's AST node covers its
    # whole file, so there's nothing useful to point at.
    (note,) = err.notes
    assert note.msg.kind is diag_kinds.MOD_QUALIFIES_PATHS
    assert '"a::' in note.msg.text()
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

    assert "type or module" in str(exc_info.value.diags[0])


def test_conflicting_extern_decl_message(compiler):
    src = "extern fn write(fd: i32, buf: *u8, count: usize) i32;\npub fn main() i32 { 0 }"

    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_EXTERN_DECLARATIONS,)

    err = exc_info.value.diags[0]
    assert err.msg.args["name"] == "write"
    typ, earlier_typ = err.msg.args["typ"], err.msg.args["earlier_typ"]
    assert isinstance(typ, typs.FnTyp) and typ.ret_typ is typs.I32
    assert isinstance(earlier_typ, typs.FnTyp) and earlier_typ.ret_typ.name == "i64"
    assert [label.msg.kind for label in err.labels] == [diag_kinds.EARLIER_DECL_HERE]


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
    err = exc_info.value.diags[0]
    assert err.notes == ()
    (label,) = err.labels
    assert label.msg is not None
    assert label.msg.kind is diag_kinds.PREVIOUS_DEFN_HERE
    harness.assert_span_at(label.span, src, "fn id[T]")


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

    err = exc_info.value.diags[0]
    assert '"get"' in str(err)
    assert "associated function" in str(err)

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "get(*self) i32 { 1 }")

    assert err.notes == ()
    (label,) = err.labels
    assert label.msg is not None
    assert label.msg.kind is diag_kinds.PREVIOUS_DEFN_HERE
    harness.assert_span_at(label.span, src, "get(*self) i32 { 0 }")


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

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "struct A")

    err = exc_info.value.diags[0]
    first, second = err.notes
    assert first.msg.kind is second.msg.kind is diag_kinds.FIELD_CONTAINS_BY_VALUE
    assert '"b"' in first.msg.text()
    harness.assert_span_at(first.span, src, "b: B")
    assert '"a"' in second.msg.text()
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

    err = exc_info.value.diags[0]
    assert (err.msg.args["given"], err.msg.args["expected"]) == (1, 0)
    assert '"Color::Red"' in str(err)
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

    err = exc_info.value.diags[0]
    assert err.msg.args["var"] == "b"
    harness.assert_span_at(err.span, src, "let b")

    assert [(note.msg.kind, note.msg.args["name"]) for note in err.notes] == [
        (diag_kinds.DEFINED_HERE, "b"),
        (diag_kinds.DEFINED_HERE, "a"),
    ]
    harness.assert_span_at(err.notes[0].span, src, "let b")
    harness.assert_span_at(err.notes[1].span, src, "let a")


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

    err = exc_info.value.diags[0]
    assert '"Y[T]"' in str(err)
    harness.assert_span_at(err.span, src, "Y[T]] { fn x")

    assert [note.msg.kind for note in err.notes] == [diag_kinds.BOUND_IN_CYCLE] * 3
    assert '"Y[T]"' in err.notes[0].msg.text()
    assert '"Z[T]"' in err.notes[1].msg.text()
    assert '"X[T]"' in err.notes[2].msg.text()
    expected_spans = [
        harness.src_position(src, text) for text in ("Y[T]] { fn x", "Z[T]] { fn y", "X[T]] { fn z")
    ]
    actual_spans = []
    for note in err.notes:
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

    err = exc_info.value.diags[0]
    assert (err.msg.args["trait"], err.msg.args["typ"]) == ("A", typs.I32)
    harness.assert_span_at(err.span, src, "impl[T: B]")

    assert [note.msg.kind for note in err.notes] == [diag_kinds.IMPL_IN_CYCLE] * 2
    assert '"<T as A>"' in err.notes[0].msg.text()
    assert '"<T as B>"' in err.notes[1].msg.text()
    expected_spans = [harness.src_position(src, text) for text in ("impl[T: B]", "impl[T: A]")]
    actual_spans = []
    for note in err.notes:
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

    err = exc_info.value.diags[0]
    assert err.msg.args["arg_num"] == 1
    assert (err.msg.args["given_typ"], err.msg.args["expected_typ"]) == (
        typs.PtrTyp(typs.U8, typs.CONST),
        typs.I32,
    )

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

    err = exc_info.value.diags[0]
    assert (err.msg.args["op"], err.msg.args["side"], err.msg.args["given_typ"]) == (
        "+",
        "left",
        typs.BOOL,
    )
    harness.assert_span_at(err.span, src, "true + 1")

    (label,) = err.labels
    assert label.msg.kind is diag_kinds.OP_HERE
    harness.assert_span_at(label.span, src, "+ 1")


def test_if_els_typ_mismatch_message(compiler):
    src = """pub fn main() i32 {
    if (true) { 1 } else { "abc" };
    return 0;
}
"""
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_BRANCH_TYPES,)

    err = exc_info.value.diags[0]
    harness.assert_span_at(err.span, src, "if (true)")

    then_label, els_label = err.labels
    assert (then_label.msg.kind, then_label.msg.args["typ"]) == (diag_kinds.IF_TYP, typs.I32)
    harness.assert_span_at(then_label.span, src, "{ 1 }")
    assert els_label.msg.kind is diag_kinds.ELSE_TYP
    assert '"*u8"' in els_label.msg.text()
    harness.assert_span_at(els_label.span, src, '{ "abc" }')


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

    err = exc_info.value.diags[0]
    harness.assert_span_at(err.span, src, "match (c)")

    assert [(note.msg.kind, note.msg.args["pattern"], note.span) for note in err.notes] == [
        (diag_kinds.UNCOVERED_PATTERN, "Color::Green", None),
        (diag_kinds.UNCOVERED_PATTERN, "Color::Blue", None),
    ]


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

    err = exc_info.value.diags[0]
    harness.assert_span_at(err.span, src, "match (true)")

    first, second = err.labels
    assert [label.msg.kind for label in err.labels] == [diag_kinds.MATCH_ARM_TYP] * 2
    assert first.msg.args["typ"] is typs.I32
    harness.assert_span_at(first.span, src, "0i32")
    assert '"*u8"' in second.msg.text()
    harness.assert_span_at(second.span, src, '"no"')


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

    assert '"id"' in str(exc_info.value.diags[0])

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

    err = exc_info.value.diags[0]
    assert (err.msg.args["param"], err.msg.args["item"]) == ("T", "id")
    harness.assert_span_at(err.span, src, "id(5)")

    (note,) = err.notes
    assert note.msg.kind is diag_kinds.GIVE_COMPTIME_ARGS
    assert '"id[' in note.msg.text()


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

    err = exc_info.value.diags[0]
    assert '"id"' in str(err)
    # Which count fills which field, not just that both appear.
    assert (err.msg.args["given"], err.msg.args["expected"]) == (2, 1)

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

    assert '"f"' in str(exc_info.value.diags[0])

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "f[i32](5)")


@pytest.mark.parametrize(
    ("src", "qualifier", "item_kind", "item_name"),
    [
        ("pub fn main() i32 { return i32::x; }", "i32::x", "type", "i32"),
        (
            "pub fn main() i32 { return array[i32, 3]::x; }",
            "array[i32, 3]::x",
            "type",
            "array[i32, 3]",
        ),
        (
            "trait Show { fn show(*self) i32; } pub fn main() i32 { return Show::x; }",
            "Show::x",
            "trait",
            "Show",
        ),
        (
            "fn f[T]() i32 { return T::x; } pub fn main() i32 { return 0; }",
            "T::x",
            "type parameter",
            "T",
        ),
        (
            "fn f[value N: usize]() i32 { return N::x; } pub fn main() i32 { return 0; }",
            "N::x",
            "value",
            "N",
        ),
    ],
)
def test_non_scope_item_cannot_qualify_path(compiler, src, qualifier, item_kind, item_name):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.PATH_QUALIFIER_KIND_MISMATCH,)

    assert f'{item_kind} "{item_name}"' in str(exc_info.value.diags[0])
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

    assert '"U"' in str(exc_info.value.diags[0])

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

    err = exc_info.value.diags[0]
    harness.assert_span_at(err.span, src, "match (o)")

    # The witness carries a payload column, which a wildcard stands for.
    assert [(note.msg.kind, note.msg.args["pattern"], note.span) for note in err.notes] == [
        (diag_kinds.UNCOVERED_PATTERN, "Option::Some(_)", None)
    ]


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

    err = exc_info.value.diags[0]
    assert (err.msg.args["given"], err.msg.args["expected"]) == (1, 2)
    assert '"Pair::Both"' in str(err)
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

    span = exc_info.value.diags[0].span
    harness.assert_span_at(span, src, "union Tree")

    err = exc_info.value.diags[0]
    (hop,) = err.notes
    assert hop.msg.kind is diag_kinds.PAYLOAD_CONTAINS_BY_VALUE
    assert '"Node"' in hop.msg.text()
    harness.assert_span_at(hop.span, src, "Tree),")


@pytest.mark.parametrize(
    ("src", "kind", "name", "at"),
    [
        (
            "pub fn main() i32 { return missing; }",
            diag_kinds.UNKNOWN_NAME,
            '"missing"',
            "missing;",
        ),
        (
            "fn f(x: Missing) {}\npub fn main() i32 { return 0; }",
            diag_kinds.UNKNOWN_NAME,
            '"Missing"',
            "Missing)",
        ),
        (
            "trait Show { fn show(*self) i32; }\nfn f(x: Show) {}\npub fn main() i32 { return 0; }",
            diag_kinds.TRAIT_USED_AS_TYPE,
            '"Show"',
            "Show) {}",
        ),
        (
            "fn f[value N: usize](x: N) {}\npub fn main() i32 { return 0; }",
            diag_kinds.VALUE_USED_AS_TYPE,
            '"N"',
            "N) {}",
        ),
        (
            "struct S {}\nfn f[T: S]() {}\npub fn main() i32 { return 0; }",
            diag_kinds.PATH_KIND_MISMATCH,
            '"S"',
            "S]",
        ),
    ],
)
def test_name_resolution_diags_name_the_item_where_it_is_used(compiler, src, kind, name, at):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (kind,)

    assert name in str(exc_info.value.diags[0])
    harness.assert_span_at(exc_info.value.diags[0].span, src, at)
