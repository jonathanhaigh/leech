# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, errors, ir_env, ir_module


def test_int_mod_var_ref_in_fn(tmp_path):
    src = """
    let a = 100;
    pub fn main() i32 {
        return a;
    }
    """
    util.check_prog_output(tmp_path, src, "", 100)


def test_str_mod_var_ref_in_fn(tmp_path):
    src = """
    let s = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(s);
        return 100;
    }
    """
    util.check_prog_output(tmp_path, src, "abcd\n", 100)


def test_int_mod_var_ref_in_mod(tmp_path):
    src = """
    let a = 100;
    let b = a;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        return b;
    }
    """
    util.check_prog_output(tmp_path, src, "", 100)


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
    util.check_prog_output(tmp_path, src, "abcd\n", 100)


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
    util.check_prog_output(tmp_path, src, "", 99)


def test_non_generic_fn_mod_var_initializer_is_fn_ref(tmp_path):
    # Evaluating the initializer also verifies that an FnRef is not rejected as
    # a temporary compile-time pointer.
    mod = util.build_ir_mod(
        tmp_path,
        "fn f() i32 { 99 }\nlet g = f;\npub fn main() i32 { g() }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "g")
    assert item is not None
    var = asserts.checked_cast(item.value, ir_module.ModVar)

    assert isinstance(var.initializer, ir_module.FnRef)
    assert var.initializer.instance.source_fn is not None
    assert var.initializer.instance.source_fn.name == "f"


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
    util.check_prog_output(tmp_path, src, "", 99)


def test_var_not_found_at_mod_scope(tmp_path):
    src = """
    let a = x;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_var_not_found_at_fn_scope(tmp_path):
    src = """
    pub fn main() i32 { x }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_var_not_found_as_assignment_target(tmp_path):
    # An assignment target is resolved through _place_mut, not _check_var_expr.
    src = """
    pub fn main() i32 {
        x = 1;
        return 0;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_var_not_found_behind_addr_of(tmp_path):
    # `&x` is resolved through _check_place, not _check_var_expr.
    src = """
    pub fn main() i32 {
        let p = &x;
        return 0;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_shadowed_mod_var(tmp_path):
    src = """
    let x = 100;
    pub fn main() i32 {
        let x = 200;
        return x;
    }
    """
    util.check_prog_output(tmp_path, src, "", 200)


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
    util.check_prog_output(tmp_path, src, "", 100)


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
    util.check_prog_output(tmp_path, src, "", 200)


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
    util.check_prog_output(tmp_path, src, "", 100)


def test_cannot_access_inner_scope(tmp_path):
    src = """
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        util.compile_str(tmp_path, src)


def test_duplicate_mod_var(tmp_path):
    src = """
    let x = 100;
    let x = 200;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        util.compile_str(tmp_path, src)


def test_duplicate_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        let x = 200;
        return 0;
    }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        util.compile_str(tmp_path, src)


def test_void_local_var(tmp_path):
    src = """
    fn f() { }
    pub fn main() i32 {
        let x = f();
        return 0;
    }
    """
    with pytest.raises(errors.VoidVarInitializerError):
        util.compile_str(tmp_path, src)


def test_void_mod_var(tmp_path):
    src = """
    fn f() { }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.VoidVarInitializerError):
        util.compile_str(tmp_path, src)


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
    util.check_prog_output(tmp_path, src, "", 1)


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
    util.check_prog_output(tmp_path, src, "", 2)


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
    util.check_prog_output(tmp_path, src, "", 7)


def test_mod_var_self_cycle(tmp_path):
    src = """
    let a = a;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CircularVarInitializerError):
        util.compile_str(tmp_path, src)


def test_mod_var_cycle(tmp_path):
    src = """
    let b = a;
    let a = b;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CircularVarInitializerError):
        util.compile_str(tmp_path, src)


def test_mod_var_three_way_cycle(tmp_path):
    src = """
    let a = b;
    let b = c;
    let c = a;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CircularVarInitializerError):
        util.compile_str(tmp_path, src)


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
    with pytest.raises(errors.CircularVarInitializerError):
        util.compile_modules(tmp_path, main=main_src, a=a_src)
