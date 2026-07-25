import pytest
from l0.l0errors import (
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
