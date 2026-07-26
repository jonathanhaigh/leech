import pytest
from l0.l0errors import (
    DuplicateItemDefnError,
    ModDoesNotExistError,
    PrivateItemAccessError,
    PrivateStructFieldAccessError,
    UnexpectedTokenError,
)
from util import check_prog_output, compile_modules


def test_import_of_module_with_syntax_error(tmp_path):
    # Parsing happens per-module (main.compile_to_ir recurses for each
    # `import`), so a syntax error in an imported module must be caught
    # and reported the same way as one in the top-level file.
    main_src = """
    import a;
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    pub fn f() i32
    """
    with pytest.raises(UnexpectedTokenError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_fn(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f("abc");
    }
    """
    a_src = """
    extern fn puts(s: *u8) i32;

    pub fn f(s: *u8) i32 {
        puts(s);
        return 101;
    }
    """
    check_prog_output(tmp_path, main_src, "abc\n", 101, a=a_src)


def test_comptime_import_fn(tmp_path):
    main_src = """
    import a;
    let x = a::f();
    pub fn main() i32 {
        return x;
    }
    """
    a_src = """
    pub fn f() i32 {
        return 101;
    }
    """
    check_prog_output(tmp_path, main_src, "", 101, a=a_src)


def test_import_private_fn(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f("abc");
    }
    """
    a_src = """
    extern fn puts(s: *u8) i32;

    fn f(s: *u8) i32 {
        puts(s);
        return 101;
    }
    """
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_private_fn_use_comptime(tmp_path):
    main_src = """
    import a;
    let y = a::f();
    pub fn main() i32 {
        return y;
    }
    """
    a_src = """
    fn f() i32 {
        return 101;
    }
    """
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_var(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::x;
    }
    """
    a_src = """
    pub let x = 11;
    """
    check_prog_output(tmp_path, main_src, "", 11, a=a_src)


def test_import_var_use_comptime(tmp_path):
    main_src = """
    import a;
    let y = a::x;
    pub fn main() i32 {
        return y;
    }
    """
    a_src = """
    pub let x = 11;
    """
    check_prog_output(tmp_path, main_src, "", 11, a=a_src)


def test_import_private_var(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        return a::x;
    }
    """
    a_src = """
    let x = 11;
    """
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_private_var_use_comptime(tmp_path):
    main_src = """
    import a;
    let y = a::x;
    pub fn main() i32 {
        return y;
    }
    """
    a_src = """
    let x = 11;
    """
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_typ(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        let x = a::T{int: 32};
        return x.int;
    }
    """
    a_src = """
    pub struct T {
        pub int: i32,
    }
    """
    check_prog_output(tmp_path, main_src, "", 32, a=a_src)


def test_import_typ_use_comptime(tmp_path):
    main_src = """
    import a;
    let x = a::T{int: 32};
    pub fn main() i32 {
        return x.int;
    }
    """
    a_src = """
    pub struct T {
        pub int: i32,
    }
    """
    check_prog_output(tmp_path, main_src, "", 32, a=a_src)


def test_import_private_typ(tmp_path):
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
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_private_typ_use_comptime(tmp_path):
    main_src = """
    import a;
    let x = a::T{int: 32};
    pub fn main() i32 {
        return x.int;
    }
    """
    a_src = """
    struct T {
        int: i32,
    }
    """
    with pytest.raises(PrivateItemAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_typ_with_var_of_same_name(tmp_path):
    # A module can hold a variable and a type of the same name, since they
    # live in separate namespaces. Resolving a::T in a type position must
    # find the struct, not the variable that happens to be declared first.
    main_src = """
    import a;
    pub fn main() i32 {
        let x = a::T{v: 32};
        return x.v;
    }
    """
    a_src = """
    pub let T = 5;

    pub struct T {
        pub v: i32,
    }
    """
    check_prog_output(tmp_path, main_src, "", 32, a=a_src)


def test_import_var_with_typ_of_same_name(tmp_path):
    # The mirror image of test_import_typ_with_var_of_same_name: a::T in a
    # value position must find the variable, not the struct type.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::T;
    }
    """
    a_src = """
    pub struct T {
        pub v: i32,
    }

    pub let T = 5;
    """
    check_prog_output(tmp_path, main_src, "", 5, a=a_src)


def test_import_var_with_private_typ_of_same_name(tmp_path):
    # The access check has to be made against the item in the namespace
    # being resolved: a private type doesn't make a public variable of the
    # same name inaccessible.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::T;
    }
    """
    a_src = """
    struct T {
        v: i32,
    }

    pub let T = 5;
    """
    check_prog_output(tmp_path, main_src, "", 5, a=a_src)


def test_import_private_struct_field_construct(tmp_path):
    # T itself is public, but priv_val isn't, so constructing a T from
    # outside a's module can't set it, even though it names a real field.
    main_src = """
    import a;
    pub fn main() i32 {
        let t = a::T{pub_val: 1, priv_val: 2};
        return 0;
    }
    """
    a_src = """
    pub struct T {
        pub pub_val: i32,
        priv_val: i32,
    }
    """
    with pytest.raises(PrivateStructFieldAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_comptime_import_private_struct_field_construct(tmp_path):
    main_src = """
    import a;
    let t = a::T{priv_val: 2};
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    pub struct T {
        priv_val: i32,
    }
    """
    with pytest.raises(PrivateStructFieldAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_private_struct_field_read(tmp_path):
    # a::make() legitimately returns a T (constructed from within a, where
    # priv_val is accessible), but main still can't read priv_val off it.
    main_src = """
    import a;
    pub fn main() i32 {
        let t = a::make();
        return t.priv_val;
    }
    """
    a_src = """
    pub struct T {
        priv_val: i32,
    }

    pub fn make() T {
        return T { priv_val: 1 };
    }
    """
    with pytest.raises(PrivateStructFieldAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_private_struct_field_write(tmp_path):
    main_src = """
    import a;
    pub fn main() i32 {
        let mut t = a::make();
        t.priv_val = 5;
        return 0;
    }
    """
    a_src = """
    pub struct T {
        priv_val: i32,
    }

    pub fn make() T {
        return T { priv_val: 1 };
    }
    """
    with pytest.raises(PrivateStructFieldAccessError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_private_struct_field_accessible_via_assoc_fn_in_defining_module(tmp_path):
    # main can't touch T::val directly, but can go through T's own public
    # assoc fns, which - being defined in the same module as T - can.
    main_src = """
    import a;
    pub fn main() i32 {
        let t = a::T::make(42);
        return a::T::get(t);
    }
    """
    a_src = """
    pub struct T {
        val: i32,
    }

    impl T {
        pub fn make(v: i32) T {
            return T { val: v };
        }

        pub fn get(t: T) i32 {
            return t.val;
        }
    }
    """
    check_prog_output(tmp_path, main_src, "", 42, a=a_src)


def test_mod_does_not_exist(tmp_path):
    main_src = """
    import nope;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(ModDoesNotExistError):
        compile_modules(tmp_path, main=main_src)


def test_duplicate_import(tmp_path):
    # Importing the same module twice binds the same name twice, which is
    # a duplicate definition like any other - not a crash.
    main_src = """
    import a;
    import a;
    pub fn main() i32 {
        return a::f();
    }
    """
    a_src = """
    pub fn f() i32 {
        return 1;
    }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_modules(tmp_path, main=main_src, a=a_src)


def test_import_chain(tmp_path):
    # main -> a -> b -> c: each module is reached only through the one
    # above it, so the whole chain has to be loaded transitively.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::viaa();
    }
    """
    a_src = """
    import b;
    pub fn viaa() i32 {
        return b::viab() + 1;
    }
    """
    b_src = """
    import c;
    pub fn viab() i32 {
        return c::base() + 10;
    }
    """
    c_src = """
    pub fn base() i32 {
        return 100;
    }
    """
    check_prog_output(tmp_path, main_src, "", 111, a=a_src, b=b_src, c=c_src)


def test_diamond_import(tmp_path):
    # main and a both import c, so c is reached by two paths. Its Foo must
    # be one type across both, or the value a::wrap() returns wouldn't be
    # usable as the c::Foo that main's own import names.
    main_src = """
    import c;
    import a;
    pub fn main() i32 {
        let f = a::wrap(7);
        let g = c::mk(35);
        return f.v + g.v;
    }
    """
    a_src = """
    import c;
    pub fn wrap(n: i32) c::Foo {
        return c::mk(n);
    }
    """
    c_src = """
    pub struct Foo {
        pub v: i32,
    }

    pub fn mk(n: i32) Foo {
        return Foo { v: n };
    }
    """
    check_prog_output(tmp_path, main_src, "", 42, a=a_src, c=c_src)


def test_diamond_import_reversed_order(tmp_path):
    # Same as test_diamond_import but importing a before c, which is the
    # order that used to leave c::Foo undeclared when a's signature
    # referring to it was declared first.
    main_src = """
    import a;
    import c;
    pub fn main() i32 {
        let f = a::wrap(7);
        let g = c::mk(35);
        return f.v + g.v;
    }
    """
    a_src = """
    import c;
    pub fn wrap(n: i32) c::Foo {
        return c::mk(n);
    }
    """
    c_src = """
    pub struct Foo {
        pub v: i32,
    }

    pub fn mk(n: i32) Foo {
        return Foo { v: n };
    }
    """
    check_prog_output(tmp_path, main_src, "", 42, a=a_src, c=c_src)


def test_import_of_typ_re_exported_by_imported_mod(tmp_path):
    # main never imports c itself; c::Foo reaches it only through a's
    # public signature, so c has to be lowered on a's behalf.
    main_src = """
    import a;
    pub fn main() i32 {
        let f = a::viaa();
        return f.v;
    }
    """
    a_src = """
    import c;
    pub fn viaa() c::Foo {
        return c::mk(42);
    }
    """
    c_src = """
    pub struct Foo {
        pub v: i32,
    }

    pub fn mk(n: i32) Foo {
        return Foo { v: n };
    }
    """
    check_prog_output(tmp_path, main_src, "", 42, a=a_src, c=c_src)


def test_import_of_var_re_exported_by_imported_mod(tmp_path):
    # A module variable reached only transitively still needs an external
    # declaration in main's output for the link to resolve.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::viaa();
    }
    """
    a_src = """
    import c;
    pub fn viaa() i32 {
        return c::cvar;
    }
    """
    c_src = """
    pub let cvar = 30;
    """
    check_prog_output(tmp_path, main_src, "", 30, a=a_src, c=c_src)


def test_circular_import(tmp_path):
    # a and b import each other. Loading registers each module before
    # building it, so the cycle resolves to the module already under
    # construction instead of recursing forever.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f();
    }
    """
    a_src = """
    import b;
    pub fn f() i32 {
        return b::g() + 1;
    }

    pub fn only_from_b() i32 {
        return 5;
    }
    """
    b_src = """
    import a;
    pub fn g() i32 {
        return a::only_from_b() * 2;
    }
    """
    check_prog_output(tmp_path, main_src, "", 11, a=a_src, b=b_src)


def test_circular_import_of_typ(tmp_path):
    # The cycle carries a struct type as well as functions, so the
    # partially-built module has to be usable for type resolution too.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f().v;
    }
    """
    a_src = """
    import b;
    pub struct Foo {
        pub v: i32,
    }

    pub fn f() Foo {
        return b::g();
    }
    """
    b_src = """
    import a;
    pub fn g() a::Foo {
        return a::Foo { v: 42 };
    }
    """
    check_prog_output(tmp_path, main_src, "", 42, a=a_src, b=b_src)


def test_self_import(tmp_path):
    # Degenerate cycle: a module importing itself resolves to itself.
    main_src = """
    import main;
    pub fn main() i32 {
        return main::helper();
    }

    pub fn helper() i32 {
        return 7;
    }
    """
    check_prog_output(tmp_path, main_src, "", 7)
