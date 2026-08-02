# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors, ir_env, ir_traits


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


def test_calling_method_through_generic_impl_not_supported_yet(tmp_path):
    # Dispatching a call to a generic trait impl's method needs
    # substituting the impl's own type parameters throughout its body -
    # a monomorphization mechanism this compiler doesn't have for
    # impl-level (as opposed to function-level) generics yet.
    src = """
    trait Show { fn show(*self) i32; }
    impl Show for i32 { fn show(*self) i32 { self.* } }
    struct Pair[A, B] { mut first: A, mut second: B }
    impl[A: Show, B: Show] Show for Pair[A, B] {
        fn show(*self) i32 { self.*.first.show() + self.*.second.show() }
    }
    pub fn main() i32 {
        let p = Pair[i32, i32] { first: 1, second: 2 };
        return p.show();
    }
    """
    with pytest.raises(AssertionError):
        util.compile_str(tmp_path, src)


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
    with pytest.raises(errors.BoundNotATraitError) as exc_info:
        util.compile_str(tmp_path, src)
    assert '"NotATrait"' in str(exc_info.value)


def test_bound_names_non_trait_on_generic_fn_call(tmp_path):
    # Here the generic body never uses the bound, so nothing catches the
    # non-trait bound until the function is actually instantiated -
    # exercising check_typ_arg_bounds rather than _resolve_bound_method.
    src = """
    struct NotATrait { a: i32 }
    fn f[T: NotATrait](x: T) i32 { return 0; }
    pub fn main() i32 {
        return f(1i32);
    }
    """
    with pytest.raises(errors.BoundNotATraitError) as exc_info:
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
    with pytest.raises(errors.BoundNotATraitError) as exc_info:
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
    assert [m.name for m in item.value.methods] == ["show"]
