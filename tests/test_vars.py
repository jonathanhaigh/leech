# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

from typing import cast

import pytest

from leech import asserts, ast, compilation, errors, ir_env, ir_module, ir_traits, ir_values, typs
from tests import harness


def test_env_rejects_bound_value_without_pointer_typ(compiler):
    ctx = compilation.Ctx()
    env = ir_env.Env(ctx, ir_traits.ImplRegistry(ctx), None)
    value = cast(
        ir_values.Value[typs.PtrTyp],
        ir_values.ComptimeInt(typs.I32, 1, None),
    )
    env.add_var("value", value)
    mod_ast = compiler.parse("let result = value;")
    (defn_ast,) = mod_ast.defns
    var_ast = asserts.checked_cast(
        asserts.checked_cast(defn_ast, ast.VarDefn).let_stmt.expr,
        ast.VarExpr,
    )

    with pytest.raises(AssertionError):
        env.resolve_var(var_ast.path)


def test_int_mod_var_ref_in_fn(compiler):
    src = """
    let a = 100;
    pub fn main() i32 {
        return a;
    }
    """
    compiler.check(src, exit_status=100)


def test_str_mod_var_ref_in_fn(compiler):
    src = """
    let s = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(s);
        return 100;
    }
    """
    compiler.check(src, stdout="abcd\n", exit_status=100)


def test_int_mod_var_ref_in_mod(compiler):
    src = """
    let a = 100;
    let b = a;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        return b;
    }
    """
    compiler.check(src, exit_status=100)


def test_str_mod_var_ref_in_mod(compiler):
    src = """
    let b = a;
    let a = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    compiler.check(src, stdout="abcd\n", exit_status=100)


def test_fn_mod_var(compiler):
    src = """
    fn f() i32 {
        return 99;
    }

    let g = f;

    pub fn main() i32 {
        return g();
    }
    """
    compiler.check(src, exit_status=99)


def test_extern_fn_value_remains_global_initializer(compiler):
    source = "extern fn puts(s: *u8) i32;\nlet p = puts;\npub fn main() i32 { 0 }"
    mod = compiler.build(source)
    item = mod.get_item(ir_env.Env.Namespace.VARS, "p")
    assert item is not None
    var = asserts.checked_cast(item.value, ir_module.ModVar)
    assert isinstance(var.initializer, ir_module.FnRef)

    ir_text = compiler.compile(source).mods["main"].llvm_ir

    assert '@"main::p" = private global i32 (i8*)* @"puts"' in ir_text


def test_non_generic_fn_mod_var_initializer_is_fn_ref(compiler):
    # Evaluating the initializer also verifies that an FnRef is not rejected as
    # a temporary compile-time pointer.
    mod = compiler.build(
        "fn f() i32 { 99 }\nlet g = f;\npub fn main() i32 { g() }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "g")
    assert item is not None
    var = asserts.checked_cast(item.value, ir_module.ModVar)

    assert isinstance(var.initializer, ir_module.FnRef)
    assert var.initializer.instance.src_fn is not None
    assert var.initializer.instance.src_fn.name == "f"


def test_fn_local_var(compiler):
    src = """
    fn f() i32 {
        return 99;
    }

    pub fn main() i32 {
        let g = f;
        return g();
    }
    """
    compiler.check(src, exit_status=99)


def test_var_not_found_at_mod_scope(compiler):
    src = """
    let a = x;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_var_not_found_at_fn_scope(compiler):
    src = """
    pub fn main() i32 { x }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_var_not_found_as_assignment_target(compiler):
    # An assignment target is resolved through _place_mut, not _check_var_expr.
    src = """
    pub fn main() i32 {
        x = 1;
        return 0;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_var_not_found_behind_addr_of(compiler):
    # `&x` is resolved through _check_place, not _check_var_expr.
    src = """
    pub fn main() i32 {
        let p = &x;
        return 0;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_shadowed_mod_var(compiler):
    src = """
    let x = 100;
    pub fn main() i32 {
        let x = 200;
        return x;
    }
    """
    compiler.check(src, exit_status=200)


def test_unshadowed_mod_var(compiler):
    src = """
    let x = 100;
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    compiler.check(src, exit_status=100)


def test_shadowed_local_var(compiler):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
            return x;
        };
    }
    """
    compiler.check(src, exit_status=200)


def test_unshadowed_local_var(compiler):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
        };
        return x;
    }
    """
    compiler.check(src, exit_status=100)


def test_cannot_access_inner_scope(compiler):
    src = """
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_duplicate_mod_var(compiler):
    src = """
    let x = 100;
    let x = 200;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.compile(src)


def test_duplicate_local_var(compiler):
    src = """
    pub fn main() i32 {
        let x = 100;
        let x = 200;
        return 0;
    }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.compile(src)


def test_void_local_var(compiler):
    src = """
    fn f() { }
    pub fn main() i32 {
        let x = f();
        return 0;
    }
    """
    with pytest.raises(errors.VoidVarInitializerError):
        compiler.compile(src)


def test_void_mod_var(compiler):
    src = """
    fn f() { }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.VoidVarInitializerError):
        compiler.compile(src)


def test_diverging_if_els_local_var_true(compiler):
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
    compiler.check(src, exit_status=1)


def test_diverging_if_els_local_var_false(compiler):
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
    compiler.check(src, exit_status=2)


def test_diverging_bare_block_local_var(compiler):
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
    compiler.check(src, exit_status=7)


def test_mod_var_self_cycle(compiler):
    src = """
    let a = a;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CircularVarInitializerError) as exc_info:
        compiler.compile(src)

    assert exc_info.value.message.message == 'Initializer of variable "a" depends on itself'
    assert [note.message for note in exc_info.value.extra] == ['Variable "a" defined here']


def test_mod_var_cycle(compiler):
    src = """
    let b = a;
    let a = b;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CircularVarInitializerError):
        compiler.compile(src)


def test_mod_var_three_way_cycle(compiler):
    src = """
    let a = b;
    let b = c;
    let c = a;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CircularVarInitializerError) as exc_info:
        compiler.compile(src)

    assert exc_info.value.message.message == 'Initializer of variable "a" depends on itself'
    assert [note.message for note in exc_info.value.extra] == [
        'Variable "a" defined here',
        'Variable "b" defined here',
        'Variable "c" defined here',
    ]


def test_cross_module_var_cycle(compiler):
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
    with pytest.raises(errors.CircularVarInitializerError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))

    assert exc_info.value.message.message == 'Initializer of variable "x" depends on itself'
    assert [note.message for note in exc_info.value.extra] == [
        'Variable "x" defined here',
        'Variable "y" defined here',
    ]


def test_mod_var_cycle_can_be_retried_after_failure(compiler):
    src = """
    let b = a;
    let a = b;
    pub fn main() i32 {
        return 0;
    }
    """
    mod = compiler.build(src)
    item = mod.get_item(ir_env.Env.Namespace.VARS, "b")
    assert item is not None
    var = asserts.checked_cast(item.value, ir_module.ModVar)

    diagnostics = []
    for _ in range(2):
        with pytest.raises(errors.CircularVarInitializerError) as exc_info:
            _ = var.initializer
        diagnostics.append(str(exc_info.value))

    assert diagnostics[0] == diagnostics[1]


def test_mod_var_diamond_dependency_is_not_a_cycle(compiler):
    src = """
    let a = 1;
    let b = a;
    let c = a;
    let d = b + c;
    pub fn main() i32 {
        return d - 2;
    }
    """
    compiler.check(src)
