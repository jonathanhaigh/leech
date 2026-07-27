import pytest

from leech.leecherrors import (
    CircularVarInitializerError,
    DuplicateItemDefnError,
    ItemNotFoundError,
    VoidVarInitializerError,
)
from util import check_prog_output, compile_modules, compile_str


def test_int_mod_var_ref_in_fn(tmp_path):
    src = """
    let a = 100;
    pub fn main() i32 {
        return a;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_str_mod_var_ref_in_fn(tmp_path):
    src = """
    let s = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(s);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)


def test_int_mod_var_ref_in_mod(tmp_path):
    src = """
    let a = 100;
    let b = a;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        return b;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_str_mod_var_ref_in_mod(tmp_path):
    src = """
    let b = a;
    let a = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)


def test_fn_mod_var(tmp_path):
    src = """
    fn f() i32 {
        return 99;
    }

    let g = f;

    pub fn main() i32 {
        return g();
    }
    """
    check_prog_output(tmp_path, src, "", 99)


def test_fn_local_var(tmp_path):
    src = """
    fn f() i32 {
        return 99;
    }

    pub fn main() i32 {
        let g = f;
        return g();
    }
    """
    check_prog_output(tmp_path, src, "", 99)


def test_var_not_found_at_mod_scope(tmp_path):
    src = """
    let a = x;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_var_not_found_at_fn_scope(tmp_path):
    src = """
    pub fn main() i32 { x }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_shadowed_mod_var(tmp_path):
    src = """
    let x = 100;
    pub fn main() i32 {
        let x = 200;
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 200)


def test_unshadowed_mod_var(tmp_path):
    src = """
    let x = 100;
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_shadowed_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
            return x;
        };
    }
    """
    check_prog_output(tmp_path, src, "", 200)


def test_unshadowed_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_cannot_access_inner_scope(tmp_path):
    src = """
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_duplicate_mod_var(tmp_path):
    src = """
    let x = 100;
    let x = 200;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


def test_duplicate_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        let x = 200;
        return 0;
    }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


def test_void_local_var(tmp_path):
    src = """
    fn f() { }
    pub fn main() i32 {
        let x = f();
        return 0;
    }
    """
    with pytest.raises(VoidVarInitializerError):
        compile_str(tmp_path, src)


def test_void_mod_var(tmp_path):
    src = """
    fn f() { }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(VoidVarInitializerError):
        compile_str(tmp_path, src)


def test_diverging_if_els_local_var_true(tmp_path):
    # Both branches diverge, so the if/else's type is `never`, not `void`
    # - unlike test_void_local_var, this is legitimate, unreachable-after
    # code, analogous to Rust's `let x: i32 = if c { return 1 } else {
    # return 2 };`.
    src = """
    pub fn main() i32 {
        let x = if (true) {
            return 1;
        } else {
            return 2;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 1)


def test_diverging_if_els_local_var_false(tmp_path):
    src = """
    pub fn main() i32 {
        let x = if (false) {
            return 1;
        } else {
            return 2;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_diverging_bare_block_local_var(tmp_path):
    # A block with no tail expression is `never`-typed (not `void`) if its
    # statements already diverged, the same distinction as above but
    # without an if/else - a bare `{ ... }` is a valid expression on its
    # own (leech.lark's `?expr: block_expr`).
    src = """
    pub fn main() i32 {
        let x = {
            return 7;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_mod_var_self_cycle(tmp_path):
    src = """
    let a = a;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CircularVarInitializerError):
        compile_str(tmp_path, src)


def test_mod_var_cycle(tmp_path):
    src = """
    let b = a;
    let a = b;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CircularVarInitializerError):
        compile_str(tmp_path, src)


def test_mod_var_three_way_cycle(tmp_path):
    src = """
    let a = b;
    let b = c;
    let c = a;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CircularVarInitializerError):
        compile_str(tmp_path, src)


def test_cross_module_var_cycle(tmp_path):
    # Circular imports are legal (see A5), so a mod-var cycle can now
    # span modules too - the cycle-check has to catch this, not just the
    # single-module case.
    main_src = """
    import a;
    pub let x = a::y;
    pub fn main() i32 {
        return 0;
    }
    """
    a_src = """
    import main;
    pub let y = main::x;
    """
    with pytest.raises(CircularVarInitializerError):
        compile_modules(tmp_path, main=main_src, a=a_src)
