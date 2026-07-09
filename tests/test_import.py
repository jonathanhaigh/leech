import pytest
from l0.l0errors import ItemNotFoundError
from util import check_prog_output, compile_modules


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
    with pytest.raises(ItemNotFoundError):
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
    with pytest.raises(ItemNotFoundError):
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
    with pytest.raises(ItemNotFoundError):
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
        int: i32,
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
        int: i32,
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
    with pytest.raises(ItemNotFoundError):
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
    with pytest.raises(ItemNotFoundError):
        compile_modules(tmp_path, main=main_src, a=a_src)
