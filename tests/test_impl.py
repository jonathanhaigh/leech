import pytest

from l0.l0errors import (
    DuplicateItemDefnError,
    ImplForNonLocalStructTypError,
    ImplForNonStructTypError,
    ItemNotFoundError,
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
    with pytest.raises(ItemNotFoundError):
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
    with pytest.raises(ItemNotFoundError):
        compile_modules(tmp_path, main=main_src, a=a_src)
