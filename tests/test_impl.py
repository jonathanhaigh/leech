# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, ast, errors, ir_env, ir_module, ir_traits, opt_util, typs


def test_assoc_fn_call(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        pub fn new() Foo { Foo { a: 42 } }
    }
    pub fn main() i32 {
        let f = Foo::new();
        return f.a;
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_generic_inherent_impl_method_found_through_registry(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn get(*self) T { return self.*.val; }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 7 };
        return b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_generic_impl_lookup_does_not_instantiate_method(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T] Box[T] {
        fn get(*self) T { return self.*.val; }
    }
    pub fn main() i32 { return 0; }
    """
    mod = util.build_ir_mod(tmp_path, src)
    box_item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, "Box")
    assert box_item is not None
    box = asserts.checked_cast(box_item.value, typs.StructTyp)
    concrete_box = box.instance((typs.I32,))
    (impl,) = mod.loader.impl_registry.find_inherent_impls(concrete_box)
    fn = opt_util.opt_unwrap(impl.get_fn_symbol("get"))
    instances_before = tuple(fn.instances)

    selection = mod.loader.impl_registry.lookup_member(concrete_box, "get", None)

    assert tuple(fn.instances) == instances_before
    selection = asserts.checked_cast(selection, ir_traits.ImplFnSelection)
    assert selection.fn is fn
    assert selection.impl_args == (typs.I32,)


def test_field_and_method_same_name_coexist(tmp_path):
    src = """
    struct Counter { get: i32 }
    impl Counter {
        fn get(*self) i32 { return self.*.get + 1; }
    }
    pub fn main() i32 {
        let counter = Counter { get: 41 };
        return counter.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_comptime_assoc_fn_call(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        pub fn new() Foo { Foo { a: 99 } }
    }
    let x = Foo::new();
    pub fn main() i32 {
        return x.a;
    }
    """
    util.check_prog_output(tmp_path, src, "", 99)


def test_multiple_assoc_fns_one_impl_block(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        pub fn new() Foo { Foo { a: 1 } }
        pub fn other() i32 { 2 }
    }
    pub fn main() i32 {
        return Foo::new().a + Foo::other();
    }
    """
    util.check_prog_output(tmp_path, src, "", 3)


def test_multiple_impl_blocks_same_struct(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        pub fn new() Foo { Foo { a: 1 } }
    }
    impl Foo {
        pub fn other() i32 { 2 }
    }
    pub fn main() i32 {
        return Foo::new().a + Foo::other();
    }
    """
    util.check_prog_output(tmp_path, src, "", 3)


def test_two_structs_with_same_assoc_fn_name(tmp_path):
    src = """
    struct Foo { a: i32 }
    struct Bar { b: i32 }
    impl Foo {
        pub fn new() Foo { Foo { a: 1 } }
    }
    impl Bar {
        pub fn new() Bar { Bar { b: 2 } }
    }
    pub fn main() i32 {
        return Foo::new().a + Bar::new().b;
    }
    """
    util.check_prog_output(tmp_path, src, "", 3)


def test_assoc_fn_lookup_does_not_leak_module_scope(tmp_path):
    src = """
    struct Foo {}
    pub fn helper() i32 { 1 }
    pub fn main() i32 {
        return Foo::helper();
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_assoc_fn_lookup_does_not_leak_builtin_scope(tmp_path):
    src = """
    struct Foo {}
    impl Foo {
        fn f() i32 { 1 }
    }
    pub fn main() i32 {
        return Foo::usize;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_impl_before_struct_defn(tmp_path):
    src = """
    impl Foo {
        pub fn new() Foo { Foo { a: 1 } }
    }
    struct Foo { a: i32 }
    pub fn main() i32 {
        return Foo::new().a;
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_duplicate_assoc_fn_name(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn new() Foo { Foo { a: 1 } }
        fn new() Foo { Foo { a: 2 } }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_str(tmp_path, src)
    assert "associated function" in str(exc_info.value)


def test_duplicate_assoc_fn_name_across_impl_blocks(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn new() Foo { Foo { a: 1 } }
    }
    impl Foo {
        fn new() Foo { Foo { a: 2 } }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        util.compile_str(tmp_path, src)


def test_field_and_receiverless_assoc_fn_same_name_coexist(tmp_path):
    src = """
    struct Foo { new: i32 }
    impl Foo {
        fn new() Foo { Foo { new: 42 } }
    }
    pub fn main() i32 { return Foo::new().new; }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_overlapping_generic_inherent_impls_with_same_fn_name_rejected_at_declaration(tmp_path):
    src = """
    struct Box[T] {}
    impl[T] Box[T] {
        fn value() i32 { 1 }
    }
    impl[U] Box[U] {
        fn value() i32 { 2 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        util.compile_str(tmp_path, src)


def test_partially_overlapping_generic_inherent_impls_with_same_fn_name_rejected(tmp_path):
    src = """
    struct Pair[A, B] {}
    impl[T] Pair[T, i32] {
        fn tag() i32 { 1 }
    }
    impl[U] Pair[bool, U] {
        fn tag() i32 { 2 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        util.compile_str(tmp_path, src)


def test_unconstrained_impl_typ_param_rejected(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T, U] Box[T] {
        fn get(*self) T { return self.*.val; }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.UnconstrainedImplTypParamError):
        util.compile_str(tmp_path, src)


def test_impl_typ_param_nested_in_self_typ_is_constrained(tmp_path):
    src = """
    struct Box[T] { val: T }
    struct Pair[A, B] { first: A, second: B }
    impl[T] Pair[Box[T], i32] {
        fn get(*self) T { return self.*.first.val; }
    }
    pub fn main() i32 { return 0; }
    """
    util.compile_str(tmp_path, src)


def test_generic_impl_instance_args_follow_declaration_order(tmp_path):
    src = """
    struct Pair[A, B] { first: A, second: B }
    impl[A, B] Pair[A, B] {
        fn first(*self) A { return self.*.first; }
    }
    pub fn main() i32 { return 0; }
    """
    mod = util.build_ir_mod(tmp_path, src)
    pair_item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, "Pair")
    assert pair_item is not None
    pair = asserts.checked_cast(pair_item.value, typs.StructTyp)
    concrete_pair = pair.instance((typs.I32, typs.BOOL))
    (impl,) = mod.loader.impl_registry.find_inherent_impls(concrete_pair)
    fn = opt_util.opt_unwrap(impl.get_fn_symbol("first"))

    inst = fn.instantiate((typs.I32, typs.BOOL))
    assert inst.args == (typs.I32, typs.BOOL)
    assert inst.impl_args == (typs.I32, typs.BOOL)
    assert inst.fn_args == ()
    assert inst is fn.instantiate((typs.I32, typs.BOOL))


def test_impl_typ_param_used_only_by_method_is_unconstrained(tmp_path):
    src = """
    struct Box[T] { val: T }
    impl[T, U] Box[T] {
        fn unused(x: U) U { return x; }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.UnconstrainedImplTypParamError):
        util.compile_str(tmp_path, src)


def test_uncalled_private_impl_method_body_is_still_typechecked(tmp_path):
    src = """
    struct Foo {}
    impl Foo {
        fn invalid(*self) i32 { return true; }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidRetTypError):
        util.compile_str(tmp_path, src)


def test_disjoint_inherent_impls_reuse_assoc_fn_name(tmp_path):
    src = """
    struct Pair[A, B] {}
    impl[T] Pair[i32, T] {
        fn tag() i32 { 1 }
    }
    impl[T] Pair[bool, T] {
        fn tag() i32 { 2 }
    }
    pub fn main() i32 { return 0; }
    """
    util.compile_str(tmp_path, src)


def test_repeated_generic_inherent_impl_is_disjoint_from_unequal_concrete_args(tmp_path):
    src = """
    struct Pair[A, B] {}
    impl[T] Pair[T, T] {
        fn tag() i32 { 1 }
    }
    impl Pair[bool, i32] {
        fn tag() i32 { 2 }
    }
    pub fn main() i32 { return 0; }
    """
    util.compile_str(tmp_path, src)


def test_recursive_generic_inherent_impl_equation_does_not_overlap(tmp_path):
    src = """
    struct Box[T] {}
    struct Pair[A, B] {}
    impl[T] Pair[T, Box[T]] {
        fn tag() i32 { 1 }
    }
    impl[U] Pair[Box[U], U] {
        fn tag() i32 { 2 }
    }
    pub fn main() i32 { return 0; }
    """
    util.compile_str(tmp_path, src)


def test_same_block_duplicate_assoc_fn_reports_second_identifier_span(tmp_path):
    src = """
    struct Foo {}
    impl Foo {
        fn duplicate() i32 { 1 }
        fn duplicate() i32 { 2 }
    }
    """
    mod_ast = util.parse_mod(tmp_path, src)
    _, impl_ast = mod_ast.defns
    assert isinstance(impl_ast, ast.ImplDefn)
    impl = ir_traits.Impl(impl_ast, None, typs.I32, (), ir_env.Env(), "main")

    first, second = (
        ir_module.SrcFnSymbol(fn_ast, impl.env, "main", recv_typ=typs.I32, impl=impl)
        for fn_ast in impl_ast.fn_defns
    )
    impl.add_fn_symbol(first)
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        impl.add_fn_symbol(second)

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "duplicate() i32 { 2 }")


def test_same_name_field_and_method_in_different_structs(tmp_path):
    # Member namespaces are per-struct: A's field `get` and B's method
    # `get` don't clash with each other.
    src = """
    struct A { get: i32 }
    struct B { n: i32 }
    impl B {
        fn get(*self) i32 { self.*.n }
    }
    pub fn main() i32 {
        let a = A { get: 5 };
        let b = B { n: 37 };
        return a.get + b.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_field_not_reachable_by_assoc_fn_path(tmp_path):
    # Fields are only reachable through a value, never a ``Foo::a`` path.
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn f() i32 { 1 }
    }
    pub fn main() i32 {
        return Foo::a;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_assoc_fn_not_usable_as_typ(tmp_path):
    # A struct's members are all values, so an associated function must not
    # resolve in a type position even though the member lookup is keyed on
    # name alone.
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn f() i32 { 1 }
    }
    fn g(p: Foo::f) i32 { return 0; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_sibling_assoc_fn_call_by_bare_name(tmp_path):
    # Associated functions are bound in their impl block's scope, which a
    # function body's scope descends from, so they can call each other without
    # qualification.
    src = """
    struct S { n: i32 }
    impl S {
        fn helper() i32 { 7 }
        fn get(*self) i32 { helper() + self.*.n }
    }
    pub fn main() i32 {
        let s = S { n: 35 };
        return s.get();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_separate_inherent_impl_blocks_do_not_share_bare_assoc_fn_names(tmp_path):
    src = """
    struct S {}
    impl S {
        fn helper() i32 { 42 }
    }
    impl S {
        fn get() i32 { helper() }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


@pytest.mark.parametrize(
    "impl_typ",
    (
        "u8",
        "*Foo",
        "[Foo; 3]",
    ),
)
def test_impl_on_non_struct_typ(impl_typ, tmp_path):
    src = f"""
    struct Foo {{}}
    impl {impl_typ} {{
        fn f() i32 {{ 1 }}
    }}
    pub fn main() i32 {{ return 0; }}
    """
    with pytest.raises(errors.ImplForNonStructTypError):
        util.compile_str(tmp_path, src)


def test_impl_on_qualified_path_typ(tmp_path):
    main_src = """
    import a;
    impl a::Foo {
        fn f() i32 { 1 }
    }
    pub fn main() i32 { return 0; }
    """
    a_src = """
    pub struct Foo {}
    """
    with pytest.raises(errors.ImplForNonLocalStructTypError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


def test_cross_module_assoc_fn_call(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        let f = a::Foo::new();
        return f.a;
    }
    """
    a_src = """
    pub struct Foo { pub a: i32 }
    impl Foo {
        pub fn new() Foo { Foo { a: 7 } }
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 7, a=a_src)


def test_private_assoc_fn_accessible_within_defining_module(tmp_path):
    # Private (the default - no `pub`) associated functions are freely
    # callable from within the same module as the struct.
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn new() Foo { Foo { a: 42 } }
    }
    pub fn main() i32 {
        return Foo::new().a;
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_cross_module_private_assoc_fn_call(tmp_path):
    # Foo itself is public, but new() isn't, so it can't be called as
    # a::Foo::new() from outside a's module even though it names a real
    # associated function.
    main_src = """
    import a;
    pub fn main() i32 {
        let f = a::Foo::new();
        return f.a;
    }
    """
    a_src = """
    pub struct Foo { pub a: i32 }
    impl Foo {
        fn new() Foo { Foo { a: 7 } }
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


def test_comptime_cross_module_private_assoc_fn_call(tmp_path):
    main_src = """
    import a;
    let f = a::Foo::new();
    pub fn main() i32 {
        return f.a;
    }
    """
    a_src = """
    pub struct Foo { pub a: i32 }
    impl Foo {
        fn new() Foo { Foo { a: 7 } }
    }
    """
    with pytest.raises(errors.PrivateItemAccessError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)
