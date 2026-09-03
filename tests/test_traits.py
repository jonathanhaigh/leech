# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, ast, compilation, errors, ir_env, ir_module, ir_traits, typs


def test_trait_impl_for_builtin_typ(tmp_path):
    src = """
    trait Show {
        fn show(*self) i32;
    }
    impl Show for i32 {
        fn show(*self) i32 { self.* + 1 }
    }
    pub fn main() i32 {
        let x: i32 = 10;
        return x.show() - 11;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_impl_for_struct(tmp_path):
    src = """
    trait Show {
        fn show(*self) i32;
    }
    struct Foo { a: i32 }
    impl Show for Foo {
        fn show(*self) i32 { self.*.a + 2 }
    }
    pub fn main() i32 {
        let f = Foo { a: 20 };
        return f.show() - 22;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_impl_with_mut_receiver(tmp_path):
    src = """
    trait Reset {
        fn reset(*mut self);
    }
    struct Counter { mut n: i32 }
    impl Reset for Counter {
        fn reset(*mut self) { self.*.n = 0; }
    }
    pub fn main() i32 {
        let mut c = Counter { n: 42 };
        c.reset();
        return c.n;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_inherent_and_trait_methods_coexist(tmp_path):
    # A struct's own inherent members are checked first - an inherent
    # method of the same name as a trait method (on a different type, so
    # no actual clash) doesn't interfere.
    src = """
    trait Show { fn show(*self) i32; }
    struct Foo { a: i32 }
    impl Foo {
        fn show(*self) i32 { self.*.a }
    }
    impl Show for i32 {
        fn show(*self) i32 { self.* + 100 }
    }
    pub fn main() i32 {
        let f = Foo { a: 7 };
        let x: i32 = 1;
        return (f.show() - 7) + (x.show() - 101);
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_inherent_impl_is_registered(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        struct Foo { a: i32 }
        impl Foo {
            fn get(*self) i32 { return self.*.a; }
        }
        """,
    )
    foo = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Foo")
    inherent_impls = mod.env.impl_registry.find_inherent_impls(foo)
    assert len(inherent_impls) == 1
    assert inherent_impls[0].trait is None
    assert inherent_impls[0].self_typ is foo
    assert inherent_impls[0].get_fn_symbol("get") is not None


def test_fn_points_at_its_impl_block(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        struct Foo { a: i32 }
        trait Show { fn show(*self) i32; }
        impl Show for Foo {
            pub fn show(*self) i32 { return self.*.a; }
        }
        """,
    )
    foo = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Foo")
    selection = mod.env.impl_registry.lookup_member(foo, "show", None)
    assert selection is not None
    method = selection.fn
    assert method.impl is not None
    assert method.impl.trait is not None
    assert method.impl.trait.name == "Show"


def test_generic_trait_impl_lookup_does_not_instantiate_method(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    struct Box[T] { val: T }
    impl[T] Show for Box[T] {
        fn show(*self) i32 { return 1; }
    }
    pub fn main() i32 { return 0; }
    """
    mod = util.build_ir_mod(tmp_path, src)
    box = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Box")
    box = asserts.checked_cast(box, typs.StructTypTemplate)
    concrete_box = box.instantiate((typs.I32,))
    trait = mod.env.get(ir_env.Env.Namespace.CONTAINERS, "Show")
    trait = asserts.checked_cast(trait, ir_traits.Trait)
    trait_impl = mod.loader.impl_registry.find_trait_impl(trait, concrete_box)
    assert trait_impl is not None
    fn = trait_impl.get_fn_symbol("show")
    assert fn is not None
    instances_before = tuple(fn.env.ctx.requested_fn_instances())

    selection = mod.loader.impl_registry.lookup_member(concrete_box, "show", None)

    assert tuple(fn.env.ctx.requested_fn_instances()) == instances_before
    selection = asserts.checked_cast(selection, ir_traits.ImplFnSelection)
    assert selection.fn is fn
    assert selection.impl_args == (typs.I32,)


def test_trait_method_call_on_generic_typ_param(tmp_path):
    src = """
    trait Show {
        fn show(*self) i32;
    }
    impl Show for i32 {
        fn show(*self) i32 { self.* + 1 }
    }
    fn double_show[T: Show](x: T) i32 {
        return x.show() + x.show();
    }
    pub fn main() i32 {
        let n: i32 = 10;
        return double_show(n) - 22;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_method_call_on_generic_typ_param_with_struct(tmp_path):
    src = """
    trait Show {
        fn show(*self) i32;
    }
    struct Foo { a: i32 }
    impl Show for Foo {
        fn show(*self) i32 { self.*.a }
    }
    fn double_show[T: Show](x: T) i32 {
        return x.show() + x.show();
    }
    pub fn main() i32 {
        let f = Foo { a: 5 };
        return double_show(f) - 10;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_bound_call_uses_trait_method_over_inherent_method(tmp_path):
    # A call on a type parameter resolves against its declared bound, even
    # when the eventual concrete type has an inherent method of that name.
    src = """
    trait Show { fn show(*self) i32; }
    struct Number {}
    impl Number {
        fn show(*self) i32 { 1 }
    }
    impl Show for Number {
        fn show(*self) i32 { 2 }
    }
    fn call_show[T: Show](x: T) i32 {
        return x.show();
    }
    pub fn main() i32 {
        let n = Number {};
        return call_show(n) - 2;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_trait_impl_method_calls_sibling(tmp_path):
    # `show` resolves against the impl's own abstract self type, so it
    # has to be remapped to this instantiation rather than called as the
    # unsubstituted template it was resolved to.
    src = """
    trait Show { fn show(*self) i32; fn twice(*self) i32; }
    struct Box[T] { mut val: T }
    impl[T] Show for Box[T] {
        fn show(*self) i32 { 5 }
        fn twice(*self) i32 { self.*.show() + self.*.show() }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 1 };
        return b.twice() - 10;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_declares_value_param(tmp_path):
    src = """
    trait Sized[N: i32] {
        fn size(*self) i32;
    }
    struct Buf[N: i32] {}
    impl[N: i32] Sized[N] for Buf[N] {
        fn size(*self) i32 { return N; }
    }
    pub fn main() i32 {
        let b = Buf[4] {};
        return b.size() - 4;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_bounded_generic_trait_impl_method_calls_sibling(tmp_path):
    # `twice` resolves `self.*.show()` against the impl's own abstract
    # `Box[T]`, where the impl's `T: Show` is a premise rather than
    # something to discharge - no concrete type is in hand to check it
    # against yet.
    src = """
    trait Show { fn show(*self) i32; fn twice(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } fn twice(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Show for Box[T] {
        fn show(*self) i32 { self.*.val.show() }
        fn twice(*self) i32 { self.*.show() + self.*.show() }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 5 };
        return b.twice() - 10;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_trait_impl_method_calls_sibling_for_non_struct_self_typ(tmp_path):
    src = """
    trait Show { fn show(*self) i32; fn twice(*self) i32; }
    impl[T] Show for array[T, 3] {
        fn show(*self) i32 { 5 }
        fn twice(*self) i32 { self.*.show() + self.*.show() }
    }
    pub fn main() i32 {
        let mut a: array[i32, 3] = array[i32, 3]{1, 2, 3};
        return a.twice() - 10;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_uncalled_sibling_of_a_generic_trait_impl_is_not_emitted(tmp_path):
    # Resolving a sibling instantiates it, and an instance exists to be
    # lowered and emitted - so instantiating a whole block at once would
    # emit methods the program never reaches.
    src = """
    trait Show { fn used(*self) i32; fn unused(*self) i32; }
    struct Box[T] { mut val: T }
    impl[T] Show for Box[T] {
        fn used(*self) i32 { 1 }
        fn unused(*self) i32 { 2 }
    }
    pub fn main() i32 {
        let b = Box[i32] { val: 0 };
        return b.used() - 1;
    }
    """
    (llir_path,) = util.compile_modules(tmp_path, main=src)
    ir_text = llir_path.read_text()
    assert "used" in ir_text
    assert "unused" not in ir_text


def test_generic_trait_impl_method_calls_free_fn(tmp_path):
    # Sibling remapping is consulted for every resolved function
    # reference in an instantiated body, not just genuine sibling calls,
    # so an ordinary free-function call goes through it too.
    src = """
    trait Show { fn show(*self) i32; }
    fn helper() i32 { return 4; }
    impl[T] Show for array[T, 3] { fn show(*self) i32 { helper() } }
    pub fn main() i32 {
        let mut a: array[i32, 3] = array[i32, 3]{1, 2, 3};
        return a.show() - 4;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_trait_impl_for_non_struct_self_typ(tmp_path):
    # A trait impl's self type isn't restricted to a struct the way an
    # inherent impl's target is, so a generic one can be built over any
    # type constructor.
    src = """
    trait Show { fn show(*self) i32; }
    impl[T] Show for array[T, 3] { fn show(*self) i32 { 7 } }
    pub fn main() i32 {
        let mut a: array[i32, 3] = array[i32, 3]{1, 2, 3};
        return a.show() - 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_non_struct_self_typs_differing_only_by_an_enum_elements_module(tmp_path):
    # The self type is qualified by recursing into it, so a nominal type
    # nested inside one has to qualify itself for the whole to be
    # distinct.
    a_src = "pub enum E(u8) { A }"
    b_src = "pub enum E(u8) { A }"
    main_src = """
    import a;
    import b;
    trait Show { fn show(*self) i32; }
    impl[T] Show for array[T, 2] { fn show(*self) i32 { 1 } }
    pub fn main() i32 {
        let mut x: array[a::E, 2] = array[a::E, 2]{a::E::A, a::E::A};
        let mut y: array[b::E, 2] = array[b::E, 2]{b::E::A, b::E::A};
        return x.show() + y.show() - 2;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src, b=b_src)


def test_two_traits_with_same_method_name_for_one_generic_typ(tmp_path):
    # Both instances are methods called `go` on the same `Box[i32]`, so
    # the self type alone doesn't tell their symbols apart - the trait is
    # what discriminates them.
    src = """
    trait A { fn go(*self) i32; }
    trait B { fn go(*self) i32; }
    struct Box[T] { mut val: T }
    impl[T] A for Box[T] { fn go(*self) i32 { 1 } }
    impl[T] B for Box[T] { fn go(*self) i32 { 2 } }
    fn call_a[T: A](x: T) i32 { return x.go(); }
    fn call_b[T: B](x: T) i32 { return x.go(); }
    pub fn main() i32 {
        let b = Box[i32] { val: 0 };
        return call_a(b) + call_b(b) - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_same_named_traits_in_different_mods(tmp_path):
    # Two distinct traits both named `Show`, both implemented for the
    # same generic struct: only the trait's own module tells the two
    # instantiated methods' symbols apart.
    a_src = "pub trait Show { fn show(*self) i32; }"
    b_src = "pub trait Show { fn show(*self) i32; }"
    main_src = """
    import a;
    import b;
    struct Box[T] { mut val: T }
    impl[T] a::Show for Box[T] { fn show(*self) i32 { 1 } }
    impl[T] b::Show for Box[T] { fn show(*self) i32 { 2 } }
    fn call_a[T: a::Show](x: T) i32 { return x.show(); }
    fn call_b[T: b::Show](x: T) i32 { return x.show(); }
    pub fn main() i32 {
        let x = Box[i32] { val: 0 };
        return call_a(x) + call_b(x) - 3;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src, b=b_src)


def test_generic_impl_body_typechecks(tmp_path):
    # A generic impl block's methods are checked eagerly, like a free
    # generic function's body - whether or not anything ever calls one.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Pair[A, B] { mut first: A, mut second: B }
    impl[A: Show, B: Show] Show for Pair[A, B] {
        fn show(*self) i32 { self.*.first.show() + self.*.second.show() }
    }
    pub fn main() i32 { return 0; }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_body_rejects_invalid_op(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    struct Box[T] { mut val: T }
    impl[T] Show for Box[T] {
        fn show(*self) i32 { self.*.val + self.*.val }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidBinOpArgTypError):
        util.compile_str(tmp_path, src)


def test_calling_method_through_generic_impl(tmp_path):
    # Dispatching to a generic trait impl's method substitutes the impl's
    # own type parameters throughout its body, the same way a generic
    # inherent impl's methods are monomorphized.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Pair[A, B] { mut first: A, mut second: B }
    impl[A: Show, B: Show] Show for Pair[A, B] {
        fn show(*self) i32 { self.*.first.show() + self.*.second.show() }
    }
    pub fn main() i32 {
        let p = Pair[i32, i32] { first: 1, second: 2 };
        return p.show() - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_trait_bound_call_through_generic_impl(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    struct Box[T] { value: T }
    impl[T] Show for Box[T] {
        fn show(*self) i32 { 1 }
    }
    fn call_show[T: Show](x: T) i32 {
        return x.show();
    }
    pub fn main() i32 {
        let b = Box[i32] { value: 0 };
        return call_show(b) - 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_trait_impl_instantiated_twice(tmp_path):
    # Each instantiation gets its own monomorphized body and its own
    # symbol; one must not stand in for the other.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    impl Show for bool { fn show(*self) i32 { 10 } }
    struct Box[T] { mut val: T }
    impl[T: Show] Show for Box[T] {
        fn show(*self) i32 { self.*.val.show() }
    }
    pub fn main() i32 {
        let a = Box[i32] { val: 7 };
        let b = Box[bool] { val: true };
        return a.show() + b.show() - 17;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_with_unsatisfied_bound_does_not_apply(tmp_path):
    # `Box[bool]` doesn't satisfy the impl's own `T: Show`, so the impl
    # doesn't apply to it and `Box[bool]` implements nothing - which is
    # what the call site's own bound check then reports.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Show for Box[T] {
        fn show(*self) i32 { self.*.val.show() }
    }
    fn call_show[T: Show](x: T) i32 { return x.show(); }
    pub fn main() i32 {
        let b = Box[bool] { val: true };
        return call_show(b);
    }
    """
    with pytest.raises(errors.UnsatisfiedBoundError):
        util.compile_str(tmp_path, src)


def test_generic_impl_with_unsatisfied_bound_provides_no_method(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Show for Box[T] {
        fn show(*self) i32 { self.*.val.show() }
    }
    pub fn main() i32 {
        let b = Box[bool] { val: true };
        return b.show();
    }
    """
    with pytest.raises(errors.NotCallableError):
        util.compile_str(tmp_path, src)


def test_generic_impl_bound_unsatisfied_by_callers_typ_param(tmp_path):
    # `f` never declares `U: Show`, so nothing proves the impl's own
    # `T: Show` for `Box[U]` - the impl doesn't apply, and that has to be
    # settled here rather than left for `f[bool]` to discover.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Show for Box[T] { fn show(*self) i32 { self.*.val.show() } }
    fn f[U](x: Box[U]) i32 { return x.show(); }
    pub fn main() i32 {
        let b = Box[bool] { val: true };
        return f(b);
    }
    """
    with pytest.raises(errors.NotCallableError):
        util.compile_str(tmp_path, src)


def test_generic_impl_bound_satisfied_by_callers_typ_param(tmp_path):
    # `U: Show` is an assumption in scope, and discharges the impl's own
    # `T: Show` without any concrete type being known.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Show for Box[T] { fn show(*self) i32 { self.*.val.show() } }
    fn f[U: Show](x: Box[U]) i32 { return x.show(); }
    pub fn main() i32 {
        let b = Box[i32] { val: 5 };
        return f(b) - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_generic_impl_bound_satisfied_through_another_generic_impl(tmp_path):
    # Selecting the impl for `Box[Box[i32]]` checks `Box[i32]: Show`,
    # which selects the same impl again - the recursion between impl
    # selection and bound checking has to bottom out.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T] { mut val: T }
    impl[T: Show] Show for Box[T] {
        fn show(*self) i32 { self.*.val.show() }
    }
    pub fn main() i32 {
        let inner = Box[i32] { val: 6 };
        let outer = Box[Box[i32]] { val: inner };
        return outer.show() - 6;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_impl_selection_descends_beyond_old_depth_limit(tmp_path):
    nested_typ = "i32"
    nested_value = "1"
    for _ in range(40):
        nested_value = f"Box[{nested_typ}] {{ val: {nested_value} }}"
        nested_typ = f"Box[{nested_typ}]"

    src = f"""
    trait Show {{ fn show(*self) i32; }}
    impl Show for i32 {{ fn show(*self) i32 {{ self.* }} }}
    struct Box[T] {{ mut val: T }}
    impl[T: Show] Show for Box[T] {{
        fn show(*self) i32 {{ self.*.val.show() }}
    }}
    pub fn main() i32 {{
        let nested = {nested_value};
        return nested.show() - 1;
    }}
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_unsatisfied_bound_on_generic_fn_call(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    fn double_show[T: Show](x: T) i32 {
        return x.show() + x.show();
    }
    pub fn main() i32 {
        let n: bool = true;
        return double_show(n);
    }
    """
    with pytest.raises(errors.UnsatisfiedBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    msg = str(exc_info.value)
    assert '"bool"' in msg
    assert '"Show"' in msg
    assert '"T"' in msg


def test_unsatisfied_bound_on_generic_struct_instantiation(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    struct Box[T: Show] { mut val: T }
    pub fn main() i32 {
        let b = Box[bool] { val: true };
        return 0;
    }
    """
    with pytest.raises(errors.UnsatisfiedBoundError):
        util.compile_str(tmp_path, src)


def test_bound_satisfied_on_generic_struct_instantiation(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Box[T: Show] { mut val: T }
    pub fn main() i32 {
        let b = Box[i32] { val: 5 };
        return b.val.show() - 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_ambiguous_method_call_between_two_traits(tmp_path):
    src = """
    trait A { fn f(*self) i32; }
    trait B { fn f(*self) i32; }
    impl A for i32 { fn f(*self) i32 { 1 } }
    impl B for i32 { fn f(*self) i32 { 2 } }
    pub fn main() i32 {
        let x: i32 = 0;
        return x.f();
    }
    """
    with pytest.raises(errors.AmbiguousMethodError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"f"' in str(exc_info.value)


def test_ambiguous_method_call_on_bound_typ_param(tmp_path):
    src = """
    trait A { fn f(*self) i32; }
    trait B { fn f(*self) i32; }
    fn call_f[T: A + B](x: T) i32 {
        return x.f();
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.AmbiguousMethodError):
        util.compile_str(tmp_path, src)


def test_bound_names_non_trait_via_method_call(tmp_path):
    # A non-trait bound is caught eagerly, while the generic body itself
    # is checked - before the function is ever called - because the body
    # calls a method on the bound type parameter.
    src = """
    struct NotATrait { a: i32 }
    fn f[T: NotATrait](x: T) i32 {
        return x.some_method();
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.InvalidComptimeBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"NotATrait"' in str(exc_info.value)


def test_bound_names_non_trait_on_generic_fn_call(tmp_path):
    # Here the generic body never uses the bound, so nothing catches the
    # non-trait bound until the function is actually instantiated -
    # exercising check_comptime_arg_bounds rather than _resolve_bound_method.
    src = """
    struct NotATrait { a: i32 }
    fn f[T: NotATrait](x: T) i32 { return 0; }
    pub fn main() i32 {
        return f(1i32);
    }
    """
    with pytest.raises(errors.InvalidComptimeBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"NotATrait"' in str(exc_info.value)


def test_bound_names_non_trait_on_generic_struct_instantiation(tmp_path):
    src = """
    struct NotATrait { a: i32 }
    struct Box[T: NotATrait] { mut val: T }
    pub fn main() i32 {
        let b = Box[i32] { val: 1 };
        return 0;
    }
    """
    with pytest.raises(errors.InvalidComptimeBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"NotATrait"' in str(exc_info.value)


def test_trait_missing_method_not_implemented(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 {}
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.TraitMethodNotImplementedError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"show"' in str(exc_info.value)
    assert '"Show"' in str(exc_info.value)


def test_trait_impl_extra_method(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 {
        fn show(*self) i32 { self.* }
        fn other(*self) i32 { 1 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ExtraMethodInImplError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"other"' in str(exc_info.value)


def test_trait_impl_duplicate_method(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 {
        fn show(*self) i32 { self.* }
        fn show(*self) bool { true }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.DuplicateItemDefnError) as exc_info:
        util.compile_str(tmp_path, src)
    assert "method" in str(exc_info.value)


def test_trait_impl_duplicate_extra_method_is_rejected_atomically(tmp_path):
    mod_ast = util.parse_mod(
        tmp_path,
        """
        trait Show { fn show(*self) i32; }
        impl Show for i32 {
            fn other(*self) i32 { 1 }
            fn other(*self) i32 { 2 }
        }
        """,
    )
    trait_ast, impl_ast = mod_ast.defns
    assert isinstance(trait_ast, ast.TraitDefn)
    assert isinstance(impl_ast, ast.ImplDefn)
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    trait = ir_traits.Trait(trait_ast, env, "main")
    trait_impl = ir_traits.Impl(impl_ast, trait, typs.I32, (), env, "main")

    for fn_ast in impl_ast.fn_defns:
        fn = ir_module.SrcFnSymbol(fn_ast, trait_impl.env, "main", recv_typ=typs.I32)
        with pytest.raises(errors.ExtraMethodInImplError):
            trait_impl.add_fn_symbol(fn)
        assert not trait_impl.fn_symbols


def test_trait_impl_method_signature_mismatch(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 {
        fn show(*self) bool { true }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.TraitMethodSignatureMismatchError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"show"' in str(exc_info.value)


def test_trait_impl_method_wrong_receiver_mutability(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 {
        fn show(*mut self) i32 { self.* }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.TraitMethodSignatureMismatchError):
        util.compile_str(tmp_path, src)


def test_conflicting_impls_of_same_trait_same_typ(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ConflictingImplsError):
        util.compile_str(tmp_path, src)


def test_partially_overlapping_generic_trait_impls_conflict(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    struct Pair[A, B] {}
    impl[T] Show for Pair[T, i32] { fn show(*self) i32 { 1 } }
    impl[U] Show for Pair[bool, U] { fn show(*self) i32 { 2 } }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ConflictingImplsError):
        util.compile_str(tmp_path, src)


@pytest.mark.parametrize(
    "impls",
    (
        """
        impl[T] Show for T { fn show(*self) i32 { 1 } }
        impl Show for i32 { fn show(*self) i32 { 2 } }
        """,
        """
        impl Show for i32 { fn show(*self) i32 { 2 } }
        impl[T] Show for T { fn show(*self) i32 { 1 } }
        """,
    ),
)
def test_blanket_trait_impl_conflicts_with_concrete_impl(tmp_path, impls):
    src = (
        """
    trait Show { fn show(*self) i32; }
    """
        + impls
        + """
    pub fn main() i32 { return 0; }
    """
    )
    with pytest.raises(errors.ConflictingImplsError):
        util.compile_str(tmp_path, src)


def test_blanket_trait_impl_is_selected_for_concrete_typ(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl[T] Show for T { fn show(*self) i32 { 42 } }
    pub fn main() i32 {
        let value: i32 = 0;
        return value.show();
    }
    """
    util.check_prog_output(tmp_path, src, "", 42)


def test_impls_of_different_traits_for_same_typ_do_not_conflict(tmp_path):
    src = """
    trait A { fn a(*self) i32; }
    trait B { fn b(*self) i32; }
    impl A for i32 { fn a(*self) i32 { 1 } }
    impl B for i32 { fn b(*self) i32 { 2 } }
    pub fn main() i32 {
        let x: i32 = 0;
        return x.a() + x.b() - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_impls_of_same_trait_for_different_int_typs_do_not_conflict(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    impl Show for u8 { fn show(*self) i32 { 7 } }
    pub fn main() i32 {
        let x: i32 = 3;
        let y: u8 = 1u8;
        return x.show() + y.show() - 10;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_orphan_impl_neither_trait_nor_typ_local(tmp_path):
    a_src = "pub trait Show { fn show(*self) i32; }"
    main_src = """
    import a;
    impl a::Show for i32 { fn show(*self) i32 { self.* } }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.OrphanImplError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)


def test_impl_local_trait_for_foreign_typ_not_orphan(tmp_path):
    a_src = "pub struct Foo { pub a: i32 }"
    main_src = """
    import a;
    trait Show { fn show(*self) i32; }
    impl Show for a::Foo { fn show(*self) i32 { self.*.a } }
    pub fn main() i32 {
        let f = a::Foo { a: 9 };
        return f.show() - 9;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_impl_foreign_trait_for_local_typ_not_orphan(tmp_path):
    a_src = "pub trait Show { fn show(*self) i32; }"
    main_src = """
    import a;
    struct Foo { a: i32 }
    impl a::Show for Foo { fn show(*self) i32 { self.*.a } }
    pub fn main() i32 {
        let f = Foo { a: 9 };
        return f.show() - 9;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_impl_for_non_trait(tmp_path):
    src = """
    struct NotATrait { a: i32 }
    impl NotATrait for i32 { fn f() i32 { 1 } }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ImplForNonTraitError):
        util.compile_str(tmp_path, src)


def test_trait_used_as_typ(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    fn f(x: Show) i32 { return 0; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.TraitUsedAsTypError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Show"' in str(exc_info.value)


def test_trait_method_missing_receiver(tmp_path):
    src = """
    trait Show { fn show() i32; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.TraitMethodMissingReceiverError):
        util.compile_str(tmp_path, src)


def test_trait_method_call_span(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    fn double_show[T: Show](x: T) i32 {
        return x.show() + x.show();
    }
    pub fn main() i32 {
        let n: bool = true;
        return double_show(n);
    }
    """
    with pytest.raises(errors.UnsatisfiedBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "double_show(n)")


def test_explicit_generic_fn_bound_error_uses_call_span(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    fn double_show[T: Show](x: T) i32 {
        return x.show() + x.show();
    }
    pub fn main() i32 {
        let n: bool = true;
        return double_show[bool](n);
    }
    """
    with pytest.raises(errors.UnsatisfiedBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "double_show[bool](n)")


def test_cross_module_trait_and_impl(tmp_path):
    a_src = """
    pub trait Show { fn show(*self) i32; }
    impl Show for i32 { pub fn show(*self) i32 { self.* + 1 } }
    """
    main_src = """
    import a;
    pub fn main() i32 {
        let x: i32 = 10;
        return x.show() - 11;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 0, a=a_src)


def test_get_trait_item_from_mod(tmp_path):
    mod = util.build_ir_mod(tmp_path, "trait Show { fn show(*self) i32; }")
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, "Show")
    assert item is not None
    assert isinstance(item.value, ir_traits.Trait)
    assert item.value.name == "Show"
    assert [m.name for m in item.value.trait_methods] == ["show"]


def test_bound_with_generic_args_parses(tmp_path):
    # Bounds are lazy: an uninstantiated generic fn never has its bounds
    # checked, so this exercises parsing alone.
    src = """
    trait Container[T] { fn get(*self) T; }
    fn unused[T: Container[i32]](x: T) i32 { return 0; }
    pub fn main() i32 { return 0; }
    """
    util.compile_str(tmp_path, src)


def test_generic_trait_exposes_typ_params(tmp_path):
    mod = util.build_ir_mod(tmp_path, "trait Container[T] { fn get(*self) T; }")
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, "Container")
    assert item is not None
    assert isinstance(item.value, ir_traits.Trait)
    assert [p.name for p in item.value.comptime_params] == ["T"]


def test_non_generic_trait_has_no_typ_params(tmp_path):
    mod = util.build_ir_mod(tmp_path, "trait Show { fn show(*self) i32; }")
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, "Show")
    assert item is not None
    assert isinstance(item.value, ir_traits.Trait)
    assert item.value.comptime_params == ()


def test_trait_application_keeps_argument_order(tmp_path):
    mod = util.build_ir_mod(tmp_path, "trait Convert[From, To] { fn convert(*self) To; }")
    item = mod.get_item(ir_env.Env.Namespace.CONTAINERS, "Convert")
    assert item is not None
    trait = asserts.checked_cast(item.value, ir_traits.Trait)

    application = ir_traits.TraitApplication(trait, (typs.BOOL, typs.I32))

    assert application.trait is trait
    assert application.args == (typs.BOOL, typs.I32)


def test_bound_with_generic_args_on_non_generic_trait(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    fn f[T: Show[i32]](x: T) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Show"' in str(exc_info.value)


def test_bound_on_generic_trait_without_typ_args(tmp_path):
    src = """
    trait Container[T] { fn get(*self) T; }
    fn f[T: Container](x: T) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.MissingComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Container"' in str(exc_info.value)


def test_bound_with_wrong_number_of_typ_args(tmp_path):
    src = """
    trait Container[T] { fn get(*self) T; }
    fn f[T: Container[i32, i32]](x: T) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.WrongNumberOfComptimeArgsError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Container"' in str(exc_info.value)


def test_checking_bound_with_generic_args_not_supported_yet(tmp_path):
    src = """
    trait Container[T] { fn get(*self) T; }
    fn f[T: Container[i32]](x: T) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(
        NotImplementedError, match="checking bounds with generic arguments isn't supported yet"
    ):
        util.compile_str(tmp_path, src)


def test_bound_typ_arg_violating_traits_own_bound(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    trait Container[T: Show] { fn get(*self) T; }
    fn f[U: Container[bool]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.UnsatisfiedBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    msg = str(exc_info.value)
    assert '"bool"' in msg
    assert '"Show"' in msg


def test_bound_typ_arg_satisfying_traits_own_bound(tmp_path):
    # Reaches the unsupported-feature guard, which means the nested bound
    # check ran and accepted i32 rather than rejecting it.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    trait Container[T: Show] { fn get(*self) T; }
    fn f[U: Container[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(
        NotImplementedError, match="checking bounds with generic arguments isn't supported yet"
    ):
        util.compile_str(tmp_path, src)


_SIBLING_PRELUDE = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    trait Container[T: Show] { fn get(*self) T; }
"""


def test_bound_referencing_earlier_sibling_typ_param(tmp_path):
    src = (
        _SIBLING_PRELUDE
        + """
    struct Pair[A, B: Container[A]] { mut first: A, mut second: B }
    pub fn main() i32 {
        let p = Pair[bool, i32] { first: true, second: 1 };
        return 0;
    }
    """
    )
    with pytest.raises(errors.UnsatisfiedBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"bool"' in str(exc_info.value)


def test_bound_referencing_later_sibling_typ_param(tmp_path):
    src = (
        _SIBLING_PRELUDE
        + """
    struct Pair[A: Container[B], B] { mut first: A, mut second: B }
    pub fn main() i32 {
        let p = Pair[i32, bool] { first: 1, second: true };
        return 0;
    }
    """
    )
    with pytest.raises(errors.UnsatisfiedBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"bool"' in str(exc_info.value)


def test_bound_referencing_sibling_typ_param_satisfied(tmp_path):
    # Reaching the unsupported-feature guard means `A` resolved to i32 and
    # passed Container's own Show bound.
    src = (
        _SIBLING_PRELUDE
        + """
    struct Pair[A, B: Container[A]] { mut first: A, mut second: B }
    pub fn main() i32 {
        let p = Pair[i32, i32] { first: 1, second: 1 };
        return 0;
    }
    """
    )
    with pytest.raises(
        NotImplementedError, match="checking bounds with generic arguments isn't supported yet"
    ):
        util.compile_str(tmp_path, src)


def _assert_recursive_trait_bound_error(exc, primary: str, cycle: list[str]) -> None:
    assert (
        exc.value.message.message == f'Trait bound "{primary}" is part of a recursive bound cycle'
    )
    assert [note.message for note in exc.value.extra] == [
        f'Trait bound "{name}" participates in this cycle' for name in cycle
    ]


def test_self_referential_trait_bound(tmp_path):
    src = """
    trait Foo[T: Foo[T]] { fn get(*self) T; }
    fn f[U: Foo[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.RecursiveTraitBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_trait_bound_error(exc_info, "Foo[T]", ["Foo[T]"])

    span = exc_info.value.message.span
    assert span is not None
    assert (span.start_line, span.start_col) == util.find_pos(src, "Foo[T]")


def test_mutually_recursive_trait_bounds(tmp_path):
    src = """
    trait A[T: B[T]] { fn a(*self) T; }
    trait B[T: A[T]] { fn b(*self) T; }
    fn f[U: A[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.RecursiveTraitBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_trait_bound_error(exc_info, "B[T]", ["B[T]", "A[T]"])


def test_growing_recursive_trait_bound_via_pointer(tmp_path):
    # The type argument grows each step, so no resolved (trait, args) key repeats.
    src = """
    trait Foo[T: Foo[*T]] { fn get(*self) T; }
    fn f[U: Foo[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.RecursiveTraitBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_trait_bound_error(exc_info, "Foo[*T]", ["Foo[*T]"])


def test_growing_recursive_trait_bound_via_array(tmp_path):
    src = """
    trait Bar[T: Bar[array[T, 2]]] { fn get(*self) T; }
    fn f[U: Bar[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.RecursiveTraitBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_trait_bound_error(exc_info, "Bar[array[T, 2]]", ["Bar[array[T, 2]]"])


def test_growing_mutually_recursive_trait_bounds(tmp_path):
    src = """
    trait P[T: Q[*T]] { fn p(*self) T; }
    trait Q[T: P[*T]] { fn q(*self) T; }
    fn f[U: P[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.RecursiveTraitBoundError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_trait_bound_error(exc_info, "Q[*T]", ["Q[*T]", "P[*T]"])


def _assert_recursive_impl_selection_error(
    exc, trait_name: str, typ_name: str, impl_names: list[str]
) -> None:
    assert (
        exc.value.message.message
        == f'Selecting an implementation of trait "{trait_name}" for type "{typ_name}" is recursive'
    )
    assert [note.message for note in exc.value.extra] == [
        f'Implementation "{name}" participates in this cycle' for name in impl_names
    ]


def test_direct_recursive_impl_selection(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    impl[T: Show] Show for T { fn show(*self) i32 { 0 } }
    pub fn main() i32 { let x: i32 = 1; return x.show(); }
    """
    with pytest.raises(errors.RecursiveImplSelectionError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_impl_selection_error(exc_info, "Show", "i32", ["<T as Show>"])


def test_mutual_recursive_impl_selection(tmp_path):
    src = """
    trait A { fn a(*self) i32; }
    trait B { fn b(*self) i32; }
    impl[T: B] A for T { fn a(*self) i32 { 0 } }
    impl[T: A] B for T { fn b(*self) i32 { 0 } }
    pub fn main() i32 { let x: i32 = 1; return x.a(); }
    """
    with pytest.raises(errors.RecursiveImplSelectionError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_impl_selection_error(
        exc_info,
        "A",
        "i32",
        ["<T as A>", "<T as B>"],
    )


def test_recursive_impl_selection_excludes_path_into_cycle(tmp_path):
    src = """
    trait A { fn a(*self) i32; }
    trait B { fn b(*self) i32; }
    trait C { fn c(*self) i32; }
    trait D { fn d(*self) i32; }
    impl[T: B] A for T { fn a(*self) i32 { 0 } }
    impl[T: C] B for T { fn b(*self) i32 { 0 } }
    impl[T: A] C for T { fn c(*self) i32 { 0 } }
    impl[T: A] D for T { fn d(*self) i32 { 0 } }
    pub fn main() i32 { let x: i32 = 1; return x.d(); }
    """
    with pytest.raises(errors.RecursiveImplSelectionError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_impl_selection_error(
        exc_info,
        "A",
        "i32",
        ["<T as A>", "<T as B>", "<T as C>"],
    )


def test_recursive_impl_selection_through_trait_owned_bound(tmp_path):
    src = """
    trait S { fn s(*self) i32; }
    trait G[T: S] { fn g(*self) T; }
    impl[T: G[i32]] S for T { fn s(*self) i32 { 0 } }
    pub fn main() i32 { let x: i32 = 1; return x.s(); }
    """
    with pytest.raises(errors.RecursiveImplSelectionError) as exc_info:
        util.compile_str(tmp_path, src)
    _assert_recursive_impl_selection_error(exc_info, "S", "i32", ["<T as S>"])


def test_calling_method_through_bound_with_generic_args_not_supported_yet(tmp_path):
    # The bound is never checked (f is never instantiated), but f's body is
    # type-checked once, which resolves `get` against U's bounds.
    src = """
    trait Container[T] { fn get(*self) T; }
    fn f[U: Container[i32]](x: U) i32 { return x.get(); }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(
        NotImplementedError,
        match="calling a method through a bound with generic arguments isn't supported yet",
    ):
        util.compile_str(tmp_path, src)


def test_same_trait_at_different_typ_args_is_not_a_cycle(tmp_path):
    # Container is reached twice with different arguments; a trait-only
    # cycle key would wrongly reject this before the unsupported guard.
    src = """
    trait Container[T] { fn get(*self) T; }
    trait Outer[A: Container[i32], B: Container[bool]] { fn o(*self) A; }
    fn f[U: Outer[i32, bool]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(
        NotImplementedError, match="checking bounds with generic arguments isn't supported yet"
    ):
        util.compile_str(tmp_path, src)


def test_bound_with_generic_args_combined_with_plain_bound_parses(tmp_path):
    src = """
    trait Show { fn show(*self) i32; }
    trait Container[T] { fn get(*self) T; }
    fn unused[T: Container[i32] + Show](x: T) i32 { return 0; }
    pub fn main() i32 { return 0; }
    """
    util.compile_str(tmp_path, src)


def test_legal_nested_bound_chain_is_not_treated_as_recursive(tmp_path):
    # Three distinct declaration-site bounds terminate without recurrence.
    src = """
    trait C[T] { fn c(*self) T; }
    trait B[T: C[T]] { fn b(*self) T; }
    trait A[T: B[T]] { fn a(*self) T; }
    fn f[U: A[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(
        NotImplementedError, match="checking bounds with generic arguments isn't supported yet"
    ):
        util.compile_str(tmp_path, src)


def test_self_as_param_typ_in_trait_method(tmp_path):
    src = """
    trait Comparable { fn cmp(*self, other: *Self) i32; }
    impl Comparable for i32 { fn cmp(*self, other: *i32) i32 { self.* - other.* } }
    pub fn main() i32 {
        let a: i32 = 7;
        let b: i32 = 7;
        return a.cmp(&b);
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_self_as_ret_typ_in_trait_method(tmp_path):
    src = """
    trait Dup { fn dup(*self) Self; }
    impl Dup for i32 { fn dup(*self) i32 { self.* } }
    pub fn main() i32 {
        let a: i32 = 3;
        return a.dup() - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_impl_may_write_self_instead_of_concrete_typ(tmp_path):
    src = """
    trait Comparable { fn cmp(*self, other: *Self) i32; }
    impl Comparable for i32 { fn cmp(*self, other: *Self) i32 { self.* - other.* } }
    pub fn main() i32 {
        let a: i32 = 7;
        let b: i32 = 7;
        return a.cmp(&b);
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_impl_with_wrong_typ_for_self_param_still_rejected(tmp_path):
    src = """
    trait Comparable { fn cmp(*self, other: *Self) i32; }
    impl Comparable for i32 { fn cmp(*self, other: *bool) i32 { 0 } }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.TraitMethodSignatureMismatchError):
        util.compile_str(tmp_path, src)


def test_self_resolves_to_typ_param_through_trait_bound(tmp_path):
    src = """
    trait Comparable { fn cmp(*self, other: *Self) i32; }
    impl Comparable for i32 { fn cmp(*self, other: *i32) i32 { self.* - other.* } }
    fn cmp_them[T: Comparable](x: T, y: *T) i32 { return x.cmp(y); }
    pub fn main() i32 {
        let a: i32 = 7;
        let b: i32 = 7;
        return cmp_them(a, &b);
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)


def test_self_reserved_as_struct_name(tmp_path):
    src = """
    struct Self { mut x: i32 }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, src)


def test_self_reserved_as_trait_name(tmp_path):
    src = """
    trait Self { fn f(*self) i32; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, src)


def test_self_reserved_as_fn_generic_param_name(tmp_path):
    src = """
    fn f[Self](x: Self) i32 { return 0; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, src)


def test_self_reserved_as_impl_generic_param_name(tmp_path):
    src = """
    struct Foo[T] { mut val: T }
    impl[Self] Foo[Self] { fn get(*self) Self { self.*.val } }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ReservedNameError):
        util.compile_str(tmp_path, src)


def test_self_does_not_resolve_in_trait_generic_param_bound(tmp_path):
    # Documented limitation: bounds resolve in an env derived from the
    # instantiation site, not from the trait, so Self isn't in scope.
    src = """
    trait Container[X] { fn c(*self) X; }
    trait Holder[T: Container[Self]] { fn h(*self) T; }
    fn f[U: Holder[i32]](x: U) i32 { return 0; }
    pub fn main() i32 {
        let n: i32 = 1;
        return f(n);
    }
    """
    with pytest.raises(errors.ItemNotFoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Self"' in str(exc_info.value)


def test_self_does_not_resolve_in_free_fn(tmp_path):
    src = """
    fn f(x: Self) i32 { return 0; }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(errors.ItemNotFoundError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"Self"' in str(exc_info.value)
