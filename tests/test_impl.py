import pytest

from l0.l0errors import (
    DuplicateItemDefnError,
    ImplForNonLocalStructTypError,
    ImplForNonStructTypError,
    ItemNotFoundError,
    PrivateItemAccessError,
)
from util import check_prog_output, compile_modules, compile_str


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
    check_prog_output(tmp_path, src, "", 42)


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
    check_prog_output(tmp_path, src, "", 99)


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
    check_prog_output(tmp_path, src, "", 3)


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
    check_prog_output(tmp_path, src, "", 3)


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
    check_prog_output(tmp_path, src, "", 3)


def test_assoc_fn_lookup_does_not_leak_module_scope(tmp_path):
    src = """
    struct Foo {}
    pub fn helper() i32 { 1 }
    pub fn main() i32 {
        return Foo::helper();
    }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


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
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


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
    check_prog_output(tmp_path, src, "", 1)


def test_duplicate_assoc_fn_name(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn new() Foo { Foo { a: 1 } }
        fn new() Foo { Foo { a: 2 } }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


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
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


def test_field_and_method_same_name_rejected(tmp_path):
    # A struct's fields and associated functions share one namespace, so a
    # field and a method can't have the same name - unlike Rust, where
    # `c.get` and `c.get()` would disambiguate them.
    src = """
    struct Counter { get: i32 }
    impl Counter {
        fn get(*self) i32 { self.*.get }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


def test_field_and_receiverless_assoc_fn_same_name_rejected(tmp_path):
    # The clash is with the shared member namespace, not with method-call
    # syntax, so a receiverless associated function collides just the same.
    src = """
    struct Foo { new: i32 }
    impl Foo {
        fn new() Foo { Foo { new: 1 } }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


def test_field_and_method_same_name_rejected_in_separate_impl_block(tmp_path):
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn f() i32 { 1 }
    }
    impl Foo {
        fn a(*self) i32 { 2 }
    }
    pub fn main() i32 { return 0; }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


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
    check_prog_output(tmp_path, src, "", 42)


def test_field_not_reachable_by_assoc_fn_path(tmp_path):
    # Sharing a namespace with associated functions doesn't make a field
    # addressable as Foo::a - fields are only reachable through a value.
    src = """
    struct Foo { a: i32 }
    impl Foo {
        fn f() i32 { 1 }
    }
    pub fn main() i32 {
        return Foo::a;
    }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


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
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_sibling_assoc_fn_call_by_bare_name(tmp_path):
    # Associated functions are bound in their struct's own scope, which a
    # function body's scope descends from, so they can call each other
    # without qualification.
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
    check_prog_output(tmp_path, src, "", 42)


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
    with pytest.raises(ImplForNonStructTypError):
        compile_str(tmp_path, src)


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
    with pytest.raises(ImplForNonLocalStructTypError):
        compile_modules(tmp_path, main=main_src, a=a_src)


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
    check_prog_output(tmp_path, main_src, "", 7, a=a_src)


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
    check_prog_output(tmp_path, src, "", 42)


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
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


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
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)
