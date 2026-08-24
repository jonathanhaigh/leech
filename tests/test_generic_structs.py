# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, ast, errors, ir_env, ir_module, typs


def _get_struct_typ(mod, name: str) -> typs.StructTyp:
    """Get the (template) struct type ``name`` declares in ``mod``."""
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, name)
    assert item is not None
    return asserts.checked_cast(item.value, typs.StructTyp)


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
    struct_expr = asserts.checked_cast(let_stmt.expr, ast.StructExpr)
    result_expr = asserts.checked_cast(fn_ast.block.expr, ast.BinOpExpr)
    field_accesses = (
        asserts.checked_cast(result_expr.lhs, ast.FieldAccessExpr),
        asserts.checked_cast(result_expr.rhs, ast.FieldAccessExpr),
    )

    _ = fn.typ_check_results
    for field_expr in struct_expr.fields:
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


def test_generic_struct_array_element(tmp_path):
    src = """
    struct Box[T] { mut val: T }
    pub fn main() i32 {
        let a = [Box[i32] { val: 1 }, Box[i32] { val: 2 }];
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
    with pytest.raises(errors.MissingTypArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Box"' in str(exc_info.value)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "Box = ")


def test_wrong_number_of_typ_args_on_generic_struct(tmp_path):
    src = """
    struct Pair[A, B] { first: A, second: B }
    pub fn main() i32 {
        let x = Pair[i32] { first: 1 };
        return 0;
    }
    """
    with pytest.raises(errors.WrongNumberOfTypArgsError) as exc_info:
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
    with pytest.raises(errors.TypArgsOnNonGenericItemError) as exc_info:
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
    with pytest.raises(errors.InfiniteSizeStructError):
        util.compile_str(tmp_path, src)


def test_generic_struct_ptr_to_self_is_finite(tmp_path):
    src = """
    struct L[T] {
        next: *L[T],
    }
    pub fn main() i32 { return 0; }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_struct_instantiation_depth_exceeded(tmp_path):
    # Each field access wraps another array layer around T, so every
    # nesting level is a genuinely distinct instantiation - never a
    # repeat - so this can only be caught by a depth cap, not the
    # infinite-size cycle check above.
    src = """
    struct L[T] {
        x: L[[T; 1]],
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.TypInstantiationDepthExceededError):
        util.compile_str(tmp_path, src)


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
    # bound in the impl block's environment, so that reference must resolve to
    # the sibling's own instance for the same concrete receiver, not the
    # still-generic original.
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
    struct Box[T] { value: T }
    impl[T] Box[T] {
        fn get(value: T) T { value }
        fn check_ref(*self) T {
            let getter = get;
            return getter(self.*.value);
        }
    }
    pub fn main() i32 {
        let box = Box[i32] { value: 9 };
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
    # Each concrete instantiation must get its own sibling-resolution
    # mapping - a leak would make one instantiation's bare `helper` call
    # resolve to a different instantiation's `helper`.
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
    box = _get_struct_typ(mod, "Box")

    assert box.instance((typs.I32,)) is box.instance((typs.I32,))
    assert box.instance((typs.I32,)) is not box.instance((typs.BOOL,))


def test_generic_struct_instance_qualified_name(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Pair[A, B] { first: A, second: B }")
    pair = _get_struct_typ(mod, "Pair")

    inst = pair.instance((typs.I32, typs.BOOL))
    assert inst.name == "Pair[i32, bool]"
    assert inst.qualified_name == "main::Pair[i32, bool]"


def test_generic_struct_field_typs_are_substituted(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Box[T] { val: T }")
    box = _get_struct_typ(mod, "Box")

    inst = box.instance((typs.I32,))
    assert inst.fields["val"].typ is typs.I32


def test_struct_fields_mapping_is_live(tmp_path):
    mod = util.build_ir_mod(tmp_path, "struct Box { val: i32 }")
    box = _get_struct_typ(mod, "Box")

    fields = box.fields
    box._members.clear()

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
