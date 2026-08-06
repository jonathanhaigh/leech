# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, errors, ir_env, typs


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


def test_generic_impl_block_sibling_method_calls_by_bare_name(tmp_path):
    # A generic-impl method's body can call a sibling method from the same
    # impl block by bare name, not just via `self.method()` - the sibling
    # is bound in the impl's own scope (see
    # StructTyp.add_assoc_fn). That bare reference must resolve to the
    # sibling's own instance for the same concrete receiver, not the
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


def test_generic_impl_block_sibling_method_calls_by_dot_call(tmp_path):
    # A generic-impl method's body can call a sibling method through
    # `self.*.method()` too (leech has no automatic dereferencing, so the
    # explicit deref is required, same as `self.*.val` for a field) -
    # resolved through lookup_member against the impl block's own abstract
    # receiver type, the same underlying Fn a bare reference finds (see
    # StructTyp.get_assoc_fn), so it needs the same per-instantiation
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
