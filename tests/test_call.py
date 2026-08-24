# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import asserts, errors, ir_env, ir_module


def test_not_callable(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        x();
        return 0;
    }
    """
    with pytest.raises(errors.NotCallableError):
        util.compile_str(tmp_path, src)


def test_comptime_not_callable(tmp_path):
    src = """
    let x = 100;
    let y = x();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.NotCallableError):
        util.compile_str(tmp_path, src)


def test_invalid_arg_typ(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f("abc");
        return 0;
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        util.compile_str(tmp_path, src)


def test_comptime_invalid_arg_typ(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f("abc");
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        util.compile_str(tmp_path, src)


def test_void_call_as_arg(tmp_path):
    src = """
    fn f() { }
    fn g(x: i32) i32 { return x; }
    pub fn main() i32 {
        return g(f());
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        util.compile_str(tmp_path, src)


def test_too_many_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f(1, 2);
        return 0;
    }
    """
    with pytest.raises(errors.TooManyArgsError):
        util.compile_str(tmp_path, src)


def test_comptime_too_many_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f(1, 2);
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.TooManyArgsError):
        util.compile_str(tmp_path, src)


def test_not_enough_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f();
        return 0;
    }
    """
    with pytest.raises(errors.NotEnoughArgsError):
        util.compile_str(tmp_path, src)


def test_comptime_not_enough_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.NotEnoughArgsError):
        util.compile_str(tmp_path, src)


def test_comptime_call_extern(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    let x = puts("abc");
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CallExternFnAtComptimeError) as exc_info:
        util.compile_str(tmp_path, src)

    span = exc_info.value.message.span
    assert span is not None
    assert span.file.src[span.start : span.end] == "extern fn puts(s: *u8) i32;"


def test_extern_fn_has_cached_bodyless_instance(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        "extern fn puts(s: *u8) i32;\npub fn main() i32 { 0 }",
    )
    item = mod.get_item(ir_env.Env.Namespace.VARS, "puts")
    assert item is not None
    decl = asserts.checked_cast(item.value, ir_module.ExternFnSymbol)

    assert tuple(decl.instances) == ()
    inst = decl.instantiate(())

    assert inst is decl.instantiate(())
    assert tuple(decl.instances) == (inst,)
    assert tuple(mod.loader.ctx.requested_fn_instances()).count(inst) == 1
    assert not inst.has_body
    assert inst.qualified_name == "puts"
