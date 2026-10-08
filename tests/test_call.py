# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import asserts, errors, ir_env, ir_module


def test_not_callable(compiler):
    src = """
    pub fn main() i32 {
        let x = 100;
        x();
        return 0;
    }
    """
    with pytest.raises(errors.NotCallableError):
        compiler.compile(src)


def test_comptime_not_callable(compiler):
    src = """
    let x = 100;
    let y = x();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.NotCallableError):
        compiler.compile(src)


def test_invalid_arg_typ(compiler):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f("abc");
        return 0;
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        compiler.compile(src)


def test_comptime_invalid_arg_typ(compiler):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f("abc");
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        compiler.compile(src)


def test_void_call_as_arg(compiler):
    src = """
    fn f() { }
    fn g(x: i32) i32 { return x; }
    pub fn main() i32 {
        return g(f());
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        compiler.compile(src)


def test_too_many_args(compiler):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f(1, 2);
        return 0;
    }
    """
    with pytest.raises(errors.TooManyArgsError):
        compiler.compile(src)


def test_comptime_too_many_args(compiler):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f(1, 2);
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.TooManyArgsError):
        compiler.compile(src)


def test_not_enough_args(compiler):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f();
        return 0;
    }
    """
    with pytest.raises(errors.NotEnoughArgsError):
        compiler.compile(src)


def test_comptime_not_enough_args(compiler):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.NotEnoughArgsError):
        compiler.compile(src)


def test_comptime_call_extern(compiler):
    src = """
    extern fn puts(s: *u8) i32;
    let x = puts("abc");
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CallExternFnAtComptimeError) as exc_info:
        compiler.compile(src)

    span = exc_info.value.message.span
    assert span is not None
    assert span.file.src[span.start : span.end] == "extern fn puts(s: *u8) i32;"


def test_extern_fn_has_cached_bodyless_instance(compiler):
    mod = compiler.build(
        "extern fn puts(s: *u8) i32;\npub fn main() i32 { 0 }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "puts")
    assert item is not None
    decl = asserts.checked_cast(item.value, ir_module.ExternFnSymbol)

    instances_before = tuple(decl.env.ctx.requested_fn_instances())
    inst = decl.instantiate(())

    assert inst is decl.instantiate(())
    assert tuple(decl.env.ctx.requested_fn_instances()) == (*instances_before, inst)
    assert tuple(mod.ctx.requested_fn_instances()).count(inst) == 1
    assert not inst.has_body
    assert inst.qualified_name == "puts"


def test_used_extern_emits_one_declaration_and_no_definition(compiler):
    src = """
    extern fn puts(s: *u8) i32;
    pub fn main() i32 { puts("hello"); return 0; }
    """

    lines = compiler.compile(src).llvm_ir.splitlines()

    assert sum(line.startswith('declare i32 @"puts"') for line in lines) == 1
    assert not any(line.startswith("define") and '@"puts"' in line for line in lines)
