# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, ast, errors, ir_env, ir_module, mono, typs


def _get_struct_typ(mod, name: str) -> typs.StructTyp:
    """Get the non-generic struct type ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, name)
    assert item is not None
    return asserts.checked_cast(item.value, typs.StructTyp)


def _get_struct_template(mod, name: str) -> typs.StructTypTemplate:
    """Get the generic struct template ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, name)
    assert item is not None
    return asserts.checked_cast(item.value, typs.StructTypTemplate)


def test_generic_and_non_generic_struct_module_items_have_distinct_types(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "struct Box[T] { val: T }\nstruct Plain { val: i32 }",
    )

    template = _get_struct_template(mod, "Box")
    plain = _get_struct_typ(mod, "Plain")

    assert not isinstance(template, typs.Typ)
    assert isinstance(plain, typs.Typ)
    assert plain.comptime_args == ()
    assert plain.template.name == "Plain"


def test_duplicate_struct_typ_param_precedes_body_error(tmp_path):
    src = """
    struct Box[T, T] { val: T }
    pub fn main() i32 { let x: NoSuchTyp = 0; return 0; }
    """

    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_str(tmp_path, src)

    assert '"T"' in str(exc_info.value)


def test_reserved_struct_typ_param_precedes_body_error(tmp_path):
    src = """
    struct Box[i32] { val: i32 }
    pub fn main() i32 { let x: NoSuchTyp = 0; return 0; }
    """

    with pytest.raises(errors.ReservedNameError) as exc_info:
        util.compile_str(tmp_path, src)

    assert '"i32"' in str(exc_info.value)


def test_generic_struct_single_typ_param(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    pub fn main() i32 {
        let b = Box[i32] { val: 5 };
        return b.val - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_multiple_typ_params(tmp_path):
    src = """
    struct Pair[A, B] { mut first: A, mut second: B }
    pub fn main() i32 {
        let p = Pair[i32, i32] { first: 3, second: 4 };
        return (p.first + p.second) - 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_field_indices_work_for_multiple_instances(tmp_path):
    src = """
    struct Pair[A, B] { first: A, second: B }
    pub fn main() i32 {
        let ints = Pair[i32, i32] { second: 40, first: 2 };
        let flags = Pair[bool, i32] { second: 0, first: true };
        if (flags.first) { return ints.second + ints.first; };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_struct_lowering_uses_typechecked_field_indices(tmp_path):
    src = """
    struct Pair[A, B] { first: A, second: B }
    pub fn main() i32 {
        let pair = Pair[i32, i32] { second: 40, first: 2 };
        pair.second + pair.first
    }
    """
    mod = util.build_ir_mod(tmp_path, src)
    item = mod.get_item(ir_env.Env.Namespace.VARS, "main")
    assert item is not None
    fn = asserts.checked_cast(item.value, ir_module.SrcFnSymbol)
    fn_ast = asserts.checked_cast(fn.ast, ast.FnDefn)
    let_stmt = asserts.checked_cast(fn_ast.block.stmts[0], ast.LetStmt)
    brace_expr = asserts.checked_cast(let_stmt.expr, ast.BraceExpr)
    result_expr = asserts.checked_cast(fn_ast.block.expr, ast.BinOpExpr)
    field_accesses = (
        asserts.checked_cast(result_expr.lhs, ast.FieldAccessExpr),
        asserts.checked_cast(result_expr.rhs, ast.FieldAccessExpr),
    )

    _ = fn.typ_check_results
    for element in brace_expr.elements:
        field_expr = asserts.checked_cast(element, ast.StructFieldExpr)
        object.__setattr__(field_expr.ident, "name", "changed_after_type_check")
    for field_access in field_accesses:
        object.__setattr__(field_access.field, "name", "changed_after_type_check")

    _ = fn.instantiate(()).cfg


def test_generic_struct_distinct_typ_args_produce_distinct_fields(tmp_path):
    src = """
    struct Pair[A, B] { mut first: A, mut second: B }
    pub fn main() i32 {
        let p = Pair[i32, bool] { first: 3, second: true };
        if (p.second) { return p.first - 3; };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_field_write(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    pub fn main() i32 {
        let mut b = Box[i32] { val: 1 };
        b.val = 42;
        return b.val;
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_nested_generic_struct_instantiation(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    pub fn main() i32 {
        let b = Box[Box[i32]] { val: Box[i32] { val: 5 } };
        return b.val.val - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_nested_same_declaration_struct_field_is_finite(tmp_path):
    src = """
    struct Box[T] { val: T }
    struct Outer[U] { nested: Box[Box[U]] }
    pub fn main() i32 {
        let outer = Outer[i32] { nested: Box[Box[i32]] { val: Box[i32] { val: 5 } } };
        return outer.nested.val.val - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_multi_param_nested_struct_does_not_count_as_growth(tmp_path):
    src = """
    struct Pair[A, B] { a: A, b: B }
    struct Holder[U] { h: Pair[U, Pair[U, U]] }
    pub fn main() i32 { return 0; }
    """
    util.compile_str(tmp_path, src)


def test_generic_struct_array_element(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    pub fn main() i32 {
        let a = array[Box[i32], 2]{Box[i32] { val: 1 }, Box[i32] { val: 2 }};
        return (a.[0].val + a.[1].val) - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_behind_pointer(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    pub fn main() i32 {
        let b = Box[i32] { val: 9 };
        let p = &b;
        return p.*.val - 9;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_used_as_generic_fn_typ_arg(tmp_path):
    # A generic struct instantiation is a perfectly ordinary type, usable
    # to apply an unrelated generic function - the two features compose.
    src = """
    struct Point { x: i32, y: i32 }
    struct Box[T] { mut val: T }
    fn id[T](v: T) T { return v; }
    pub fn main() i32 {
        let b = id(Box[i32] { val: 5 });
        return b.val - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_fn_returning_generic_struct(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    fn wrap[T](x: T) Box[T] { return Box[T] { val: x }; }
    pub fn main() i32 {
        let n: i32 = 5;
        let b = wrap(n);
        return b.val - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_cross_module(tmp_path):
    a_src = """
    pub struct Pair[A, B] { pub mut first: A, pub mut second: B }
    """
    main_src = """
    import a;
    pub fn main() i32 {
        let p = a::Pair[i32, i32] { first: 3, second: 4 };
        return (p.first + p.second) - 7;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_generic_struct_instance_merges_across_modules(tmp_path):
    # Both a (compiled as its own root) and main independently request
    # Pair[i32, i32] - each module declares its own identified LLVM struct
    # type under the same name, which is fine: unlike function symbols,
    # per-module type declarations don't need linkonce_odr to coexist.
    a_src = """
    pub struct Pair[A, B] { pub mut first: A, pub mut second: B }
    pub fn make_pair() i32 {
        let p = Pair[i32, i32] { first: 1, second: 2 };
        return p.first + p.second;
    }
    """
    main_src = """
    import a;
    pub fn main() i32 {
        let p = a::Pair[i32, i32] { first: 3, second: 4 };
        return (p.first + p.second) + a::make_pair() - 10;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_bare_reference_to_generic_struct_requires_typ_args(tmp_path):
    src = """
    struct Box[T] { val: T }
    pub fn main() i32 {
        let x: Box = Box[i32] { val: 1 };
        return 0;
    }
    """
    with pytest.raises(errors.MissingComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Box"' in str(exc_info.value)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "Box = ")


def test_bare_generic_struct_assoc_fn_scope_requires_typ_args(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn make(v: T) Box[T] { Box[T] { val: v } }
    }
    pub fn main() i32 {
        let box = Box::make(7);
        return box.val;
    }
    """

    with pytest.raises(errors.MissingComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)

    assert '"Box"' in str(exc_info.value)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "Box::make")


def test_generic_struct_assoc_fn_scope_accepts_args_on_struct_seg(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn make(v: T) Box[T] { Box[T] { val: v } }
    }
    pub fn main() i32 {
        let box = Box[i32]::make(42);
        return box.val;
    }
    """

    util.check_prog_output(tmp_path, src, "", 42)


def test_cross_module_generic_struct_assoc_fn_scope(tmp_path):
    a_src = """
    pub struct Box[T] { pub val: T }
    impl[T] Box[T] {
        pub fn make(v: T) Box[T] { Box[T] { val: v } }
    }
    """
    main_src = """
    import a;
    pub fn main() i32 {
        let box = a::Box[i32]::make(7);
        return box.val;
    }
    """

    util.check_prog_output(tmp_path, main_src, "", 7, a=a_src)


def test_generic_struct_assoc_fn_scope_checks_arg_arity(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn make(v: T) Box[T] { Box[T] { val: v } }
    }
    pub fn main() i32 {
        let box = Box[i32, bool]::make(42);
        return box.val;
    }
    """

    with pytest.raises(errors.WrongNumberOfComptimeArgsError):
        util.compile_str(tmp_path, src)


def test_generic_struct_assoc_fn_scope_forwards_value_param(tmp_path):
    src = """
    struct Buf[value N: usize] { val: i32 }
    impl[value N: usize] Buf[N] {
        fn make(v: i32) Buf[N] { Buf[N] { val: v } }
    }
    fn make_buf[value N: usize](v: i32) Buf[N] { Buf[N]::make(v) }
    pub fn main() i32 {
        let buf = make_buf[4](42);
        return buf.val;
    }
    """

    util.check_prog_output(tmp_path, src, "", 42)


def test_generic_struct_assoc_fn_scope_forwards_typ_param(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn make(v: T) Box[T] { Box[T] { val: v } }
    }
    fn make_box[T](v: T) Box[T] { Box[T]::make(v) }
    pub fn main() i32 {
        let box = make_box[i32](42);
        return box.val;
    }
    """

    util.check_prog_output(tmp_path, src, "", 42)


def test_comptime_args_on_non_generic_assoc_fn_scope(tmp_path):
    src = """
    struct Foo { val: i32 }
    impl Foo {
        fn make(v: i32) Foo { Foo { val: v } }
    }
    pub fn main() i32 {
        let foo = Foo[i32]::make(42);
        return foo.val;
    }
    """

    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        util.compile_str(tmp_path, src)

    assert '"Foo"' in str(exc_info.value)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "Foo[i32]::make")


def test_wrong_number_of_typ_args_on_generic_struct(tmp_path):
    src = """
    struct Pair[A, B] { first: A, second: B }
    pub fn main() i32 {
        let x = Pair[i32] { first: 1 };
        return 0;
    }
    """
    with pytest.raises(errors.WrongNumberOfComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Pair"' in str(exc_info.value)


def test_explicit_typ_args_on_non_generic_struct(tmp_path):
    src = """
    struct Foo { a: i32 }
    pub fn main() i32 {
        let x: Foo[i32] = Foo { a: 1 };
        return 0;
    }
    """
    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Foo"' in str(exc_info.value)


def test_generic_struct_duplicate_field(tmp_path):
    src = """
    struct Pair[A, B] {
        a: A,
        a: B,
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateFieldInStructDefnError):
        util.compile_str(tmp_path, src)


def test_struct_value_param_typ_resolves_sibling_typ_param(tmp_path):
    # The declared type is looked up with the siblings in scope, so `T`
    # is rejected for what it is rather than reported as unknown.
    src = """
    struct S[T, value N: T] {}
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidValueParamTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_value_param_used_as_struct_field_typ_is_rejected(tmp_path):
    src = """
    struct S[value N: usize] { f: N }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ValueUsedAsTypError):
        util.compile_str(tmp_path, src)


def test_generic_struct_infinite_size_via_own_typ_param(tmp_path):
    # Regardless of what T ends up being, L[T] always directly contains
    # another L[T] by value - infinite size, whether or not anything in
    # the program ever instantiates L.
    src = """
    struct L[T] {
        x: L[T],
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InfiniteSizeStructError) as exc_info:
        util.compile_str(tmp_path, src)

    assert len(exc_info.value.extra) == 1
    assert exc_info.value.extra[0].message == 'Field "x" of struct "L" contains "L[T]" by value'


def test_generic_struct_ptr_to_self_is_finite(tmp_path):
    src = """
    struct L[T] {
        next: *L[T],
    }
    pub fn main() i32 { return 0; }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_growing_generic_struct_declaration_cycle(tmp_path):
    # Every field adds an array layer, so exact type identity never repeats.
    # The declaration still recurs with a structurally growing argument.
    src = """
    struct L[T] {
        x: L[array[T, 1]],
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InfiniteSizeStructError) as exc_info:
        util.compile_str(tmp_path, src)

    assert exc_info.value.message.message == 'Struct "L" has infinite size'
    assert len(exc_info.value.extra) == 1
    assert exc_info.value.extra[0].message == (
        'Field "x" of struct "L" contains "L[array[T, 1]]" by value'
    )


def test_growing_generic_struct_declaration_cycle_through_pointer_arg(tmp_path):
    src = """
    struct L[T] { x: L[*T] }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InfiniteSizeStructError) as exc_info:
        util.compile_str(tmp_path, src)

    assert [note.message for note in exc_info.value.extra] == [
        'Field "x" of struct "L" contains "L[*T]" by value'
    ]


def test_mutual_growing_generic_struct_declaration_cycle(tmp_path):
    src = """
    struct A[T] { x: B[T] }
    struct B[T] { y: A[array[T, 1]] }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InfiniteSizeStructError) as exc_info:
        util.compile_str(tmp_path, src)

    assert [note.message for note in exc_info.value.extra] == [
        'Field "x" of struct "A" contains "B[T]" by value',
        'Field "y" of struct "B[T]" contains "A[array[T, 1]]" by value',
    ]


def test_generic_struct_nested_cycle_keeps_nested_root_name(tmp_path):
    src = """
    struct A[T] { x: B[T] }
    struct B[T] { y: B[T] }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InfiniteSizeStructError) as exc_info:
        util.compile_str(tmp_path, src)

    assert exc_info.value.message.message == 'Struct "B[T]" has infinite size'
    assert [note.message for note in exc_info.value.extra] == [
        'Field "y" of struct "B[T]" contains "B[T]" by value'
    ]


def test_generic_impl_block_body_typechecks(tmp_path):
    # A generic impl block's methods are checked eagerly, the same as a
    # free generic function's body - whether or not main ever calls one.
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) T { self.*.val }
    }
    pub fn main() i32 { return 0; }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_block_method_callable_through_concrete_instantiation(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) T { self.*.val }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 42 };
        return b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_generic_impl_block_method_mutates_and_reads_through_typ_param(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn set(*mut self, v: T) { self.*.val = v; }
        fn get(*self) T { self.*.val }
    }
    pub fn main() i32 {
        let mut b = Box[i32] { val: 1 };
        b.set(9);
        return b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 9)


def test_generic_impl_block_method_called_through_distinct_instantiations(tmp_path):
    # Box[i32] and Box[bool] each monomorphize their own `get`.
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) T { self.*.val }
    }
    pub fn main() i32 {
        let a = Box[i32] { val: 3 };
        let b = Box[bool] { val: true };
        let x = a.get();
        if (b.get()) { return x - 3; };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_block_method_instances_get_distinct_mangled_symbols(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) T { self.*.val }
    }
    pub fn main() i32 {
        let a = Box[i32] { val: 1 };
        let b = Box[bool] { val: true };
        let x = a.get();
        let y = b.get();
        return 0;
    }
    """
    (llir_path,) = util.compile_modules(tmp_path, main=src)
    ir_text = llir_path.read_text()
    assert '@"main::Box[i32]::get"' in ir_text
    assert '@"main::Box[bool]::get"' in ir_text


def test_instances_differing_only_by_a_typ_argument_s_module(tmp_path):
    # `Box`'s own module qualifies the instance, but the argument types
    # need qualifying too - two same-named structs from different modules
    # are different types and must not share a symbol.
    a_src = "pub struct Foo { pub val: i32 }"
    b_src = "pub struct Foo { pub val: i32 }"
    main_src = """
    import a;
    import b;
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) i32 { 1 }
    }
    pub fn main() i32 {
        let x = Box[a::Foo] { val: a::Foo { val: 0 } };
        let y = Box[b::Foo] { val: b::Foo { val: 0 } };
        return x.get() + y.get() - 2;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src, b=b_src)


def test_free_generic_fn_instances_differing_only_by_an_arguments_module(tmp_path):
    # A generic function's instances are mangled with their type
    # arguments too, so those need qualifying just as a generic struct's
    # do.
    a_src = "pub struct Foo { pub val: i32 }"
    b_src = "pub struct Foo { pub val: i32 }"
    main_src = """
    import a;
    import b;
    fn id[T](v: T) T { return v; }
    pub fn main() i32 {
        let x = id(a::Foo { val: 1 });
        let y = id(b::Foo { val: 2 });
        return x.val + y.val - 3;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src, b=b_src)


def test_instances_differing_only_by_an_enum_arguments_module(tmp_path):
    # Same as above for an enum type argument. An enum is nominal, so two
    # same-named ones from different modules are as distinct as two
    # structs are, and must not share a symbol.
    a_src = "pub enum E(u8) { A }"
    b_src = "pub enum E(u8) { A }"
    main_src = """
    import a;
    import b;
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) i32 { 1 }
    }
    pub fn main() i32 {
        let x = Box[a::E] { val: a::E::A };
        let y = Box[b::E] { val: b::E::A };
        return x.get() + y.get() - 2;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src, b=b_src)


def test_generic_inherent_impl_does_not_inherit_struct_typ_param_name(tmp_path):
    src = """
    struct Box[A] { val: A }
    impl[T] Box[T] {
        fn bad(x: A) i32 { 0 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ItemNotFoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"A"' in str(exc_info.value)


def test_generic_inherent_impl_with_unsatisfied_bound_does_not_apply(tmp_path):
    # An inherent impl's own bounds gate it the same way a trait impl's
    # do: `Box[bool]` doesn't satisfy `T: Show`, so it has no `get`.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Box[T] {
        fn get(*self) i32 { self.*.val.show() }
    }
    pub fn main() i32 {
        let b = Box[bool] { val: true };
        return b.get();
    }
    """
    with pytest.raises(errors.NotCallableError):
        util.compile_str(tmp_path, src)


def test_bounded_generic_inherent_impl_method_calls_sibling(tmp_path):
    # `outer` resolves `get` against the impl's own abstract `Box[T]`,
    # where `T: Show` is the impl's premise and nothing concrete is in
    # hand to check it against.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Box[T] {
        fn get(*self) i32 { self.*.val.show() }
        fn outer(*self) i32 { self.*.get() }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 5 };
        return b.outer() - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_bounded_generic_inherent_impl_method_called_on_abstract_typ(tmp_path):
    # `use_box`'s own `Box[U]` isn't the `Box[T]` the impl registered its
    # methods on, so finding `get` scans for a structurally matching
    # generic impl. `U` is abstract, so the impl's `T: Show` can't be
    # decided here - it's discharged where `use_box` is instantiated.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Box[T] {
        fn get(*self) i32 { self.*.val.show() }
    }
    fn use_box[U: Show](b: Box[U]) i32 { return b.get(); }
    pub fn main() i32 {
        let b = Box[i32] { val: 5 };
        return use_box(b) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_inherent_impl_bound_unsatisfied_by_callers_typ_param(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Box[T] { fn get(*self) i32 { self.*.val.show() } }
    fn f[U](x: Box[U]) i32 { return x.get(); }
    pub fn main() i32 {
        let b = Box[bool] { val: true };
        return f(b);
    }
    """
    with pytest.raises(errors.NotCallableError):
        util.compile_str(tmp_path, src)


def test_declared_bound_discharges_a_structs_own_bound(tmp_path):
    # `Box[U]` needs `U: Show`, which `wrap` declares. Nothing concrete is
    # in hand, so only the assumption in scope can prove it.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T: Show] { mut val: T }
    fn wrap[U: Show](v: U) i32 {
        let b = Box[U] { val: v };
        return b.val.show();
    }
    pub fn main() i32 {
        let v: i32 = 3;
        return wrap(v) - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_typ_param_bound_resolves_in_its_own_declaring_mod(tmp_path):
    # `U`'s bound is spelled `a::Show`, which resolves only in `main`,
    # where `U` is declared - not in `a`, whose scope is where the impl
    # being matched against was written.
    a_src = """
    pub trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    pub struct Box[T] { pub mut val: T }
    impl[T: Show] Box[T] { pub fn get(*self) i32 { 5 } }
    """
    main_src = """
    import a;
    fn f[U: a::Show](x: a::Box[U]) i32 { return x.get(); }
    pub fn main() i32 {
        let b = a::Box[i32] { val: 5 };
        return f(b) - 5;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_generic_inherent_impl_with_satisfied_bound_applies(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Box[T] {
        fn get(*self) i32 { self.*.val.show() }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 5 };
        return b.get() - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_block_method_calls_free_generic_function(tmp_path):
    # A generic-impl method's own body can request a free generic function
    # instance the compiled module's own bodies never directly request -
    # discovery must find that instance too, not just ones reachable from
    # main() directly.
    src = """
    fn id[T](v: T) T { return v; }
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) T { id(self.*.val) }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 42 };
        return b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_free_generic_fn_dot_calls_generic_impl_method_through_typ_param(tmp_path):
    # A free generic function's own type parameter can flow into a struct
    # type it dot-calls a method on (`b: *Box[T]`). TypCheck resolves that
    # dot-call against Box[T] parameterized by use_box's own T - a distinct
    # instantiation from the impl block's own Box[T] - so the FnInstance it
    # finds and caches is itself still abstract. Lowering each of use_box's
    # own concrete instantiations must re-derive the right concrete
    # instance from that cached one, not reuse it as-is.
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn get(*self) T { self.*.val }
    }
    fn use_box[T](b: *Box[T]) T { b.*.get() }
    pub fn main() i32 {
        let a = Box[i32] { val: 42 };
        let c = Box[bool] { val: true };
        let x = use_box(&a);
        if (use_box(&c)) { return x - 42; };
        return 99;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_block_sibling_method_calls_by_bare_name(tmp_path):
    # A generic-impl method's body can call a sibling method from the same
    # impl block by bare name, not just via `self.method()`. The sibling is
    # bound in the impl block's environment with the impl's abstract arguments;
    # lowering substitutes the concrete receiver arguments before instantiation.
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn helper(*self) T { self.*.val }
        fn get(*self) T { helper(self) }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 42 };
        return b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_bare_sibling_reference_in_generic_impl_needs_no_fn_args(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn get(val: T) T { val }
        fn check_ref(*self) T {
            let getter = get;
            return getter(self.*.val);
        }
    }
    pub fn main() i32 {
        let box = Box[i32] { val: 9 };
        return box.check_ref();
    }
    """

    util.check_prog_output(tmp_path, src, "", 9)


def test_generic_impl_block_sibling_method_calls_by_dot_call(tmp_path):
    # A generic-impl method's body can call a sibling method through
    # `self.*.method()` too (leech has no automatic dereferencing, so the
    # explicit deref is required, same as `self.*.val` for a field) -
    # resolved through lookup_member against the impl block's own abstract
    # receiver type, the same underlying Fn a bare reference finds, so it
    # needs the same per-instantiation
    # substitution.
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn helper(*self) T { self.*.val }
        fn get(*self) T { self.*.helper() }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 42 };
        return b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_generic_impl_block_sibling_method_calls_across_instantiations(tmp_path):
    # Each concrete body must substitute its own arguments into the sibling
    # application; sharing them would call a different `helper` instance.
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn helper(*self) T { self.*.val }
        fn get(*self) T { helper(self) }
    }
    pub fn main() i32 {
        let a = Box[i32] { val: 3 };
        let b = Box[u8] { val: 7u8 };
        let c = Box[bool] { val: true };
        if (a.get() != 3) { return 1; };
        if (b.get() != 7u8) { return 2; };
        if (c.get()) {
            return 0;
        };
        return 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_block_body_rejects_invalid_op_on_typ_param(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    impl[T] Box[T] {
        fn bad(*self) T { self.*.val + self.*.val }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidBinOpArgTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"T"' in str(exc_info.value)


def test_generic_struct_instance_caches_by_typ_args(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Box[T] { val: T }")
    box = _get_struct_template(mod, "Box")

    i32_inst = box.instantiate((typs.I32,))

    assert i32_inst is box.instantiate((typs.I32,))
    assert i32_inst is not box.instantiate((typs.BOOL,))
    assert tuple(mod.loader.ctx.requested_struct_instances()).count(i32_inst) == 1


def test_mono_discovers_struct_requested_while_resolving_fields(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        struct Inner[T] { val: T }
        struct Outer[T] { inner: Inner[T] }
        """,
    )
    outer = _get_struct_template(mod, "Outer")
    outer.instantiate((typs.I32,))

    result = mono.discover(mod)
    names = {inst.qualified_name for inst in result.struct_instances}

    assert names == {"main::Outer[i32]", "main::Inner[i32]"}


def test_codegen_accepts_forward_reference_to_nested_generic_struct(tmp_path):
    src = """
    struct Inner[T] { val: T }
    struct Outer[T] { inner: Inner[T] }
    pub fn main() i32 {
        let outer = Outer[i32] { inner: Inner[i32] { val: 7 } };
        return outer.inner.val - 7;
    }
    """

    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_instance_qualified_name(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Pair[A, B] { first: A, second: B }")
    pair = _get_struct_template(mod, "Pair")

    inst = pair.instantiate((typs.I32, typs.BOOL))
    assert inst.name == "Pair[i32, bool]"
    assert inst.qualified_name == "main::Pair[i32, bool]"


def test_generic_struct_field_typs_are_substituted(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Box[T] { val: T }")
    box = _get_struct_template(mod, "Box")

    inst = box.instantiate((typs.I32,))
    assert inst.fields["val"].typ is typs.I32


def test_struct_fields_mapping_is_live(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Box { val: i32 }")
    box = _get_struct_typ(mod, "Box")

    fields = box.fields
    box._fields.clear()

    assert fields == {}


def test_impl_on_generic_struct_target_qualified_path(tmp_path):
    main_src = """
    import a;
    impl a::Pair[i32] {
        fn f() i32 { 1 }
    }
    pub fn main() i32 { return 0; }
    """
    a_src = """
    pub struct Pair[T] { val: T }
    """
    with pytest.raises(errors.ImplForNonLocalStructTypError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


def test_struct_value_param_used_in_array_field(tmp_path):
    src = """
    struct Buf[T, value N: usize] {
        data: array[T, N],
    }
    pub fn main() i32 {
        let buf = Buf[i32, 3] { data: array[i32, 3]{1, 2, 3} };
        return buf.data.[0] + buf.data.[1] + buf.data.[2] - 6;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_struct_value_param_mangled_name(tmp_path):
    src = """
    struct Buf[T, value N: usize] { data: array[T, N] }
    pub fn main() i32 {
        let buf = Buf[i32, 4] { data: array[i32, 4]{1, 2, 3, 4} };
        return buf.data.[0] - 1;
    }
    """
    ir_text = util.compile_str(tmp_path, src).read_text()
    assert '%"main::Buf[i32, 4]"' in ir_text
