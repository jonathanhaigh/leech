# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import signal

import util

from leech import ir_module, ir_values


def test_panic_usable_with_no_import(tmp_path):
    main_src = """
    pub fn main() i32 {
        panic("boom");
        return 0;
    }
    """
    util.check_prog_output(tmp_path, main_src, "boom\n", -signal.SIGABRT)


def test_assert_true_is_a_no_op(tmp_path):
    main_src = """
    pub fn main() i32 {
        assert(true);
        return 42;
    }
    """
    util.check_prog_output(tmp_path, main_src, "", 42)


def test_assert_false_panics(tmp_path):
    main_src = """
    pub fn main() i32 {
        assert(false);
        return 0;
    }
    """
    util.check_prog_output(tmp_path, main_src, "assertion failed\n", -signal.SIGABRT)


def test_own_panic_shadows_prelude_panic_within_its_own_module(tmp_path):
    # A module defining its own `fn panic(...)` shadows the ambient
    # prelude binding, but only within that module - the same
    # child-scope-shadows-ancestor guarantee as usize/isize/bool.
    main_src = """
    import a;
    fn panic(msg: *u8) i32 {
        return 99;
    }
    pub fn main() i32 {
        let x = panic("not the real panic");
        return x + a::uses_real_panic();
    }
    """
    a_src = """
    pub fn uses_real_panic() i32 {
        panic("from a");
        return 0;
    }
    """
    util.check_prog_output(tmp_path, main_src, "from a\n", -signal.SIGABRT, a=a_src)


def test_synthesized_check_uses_real_panic_even_when_shadowed(tmp_path):
    # Unlike an explicit call, a compiler-synthesized safety check (here,
    # integer overflow) always calls the real prelude panic - never a
    # module's own shadowing definition - so user code can't opt out of
    # the language's own safety guarantees just by defining a function
    # called `panic`.
    main_src = """
    fn panic(msg: *u8) i32 {
        return 99;
    }
    pub fn main() i32 {
        let x: u8 = 255;
        let y = x + 1u8;
        return 0;
    }
    """
    util.check_prog_output(tmp_path, main_src, "integer overflow\n", -signal.SIGABRT)


def test_source_and_synthesized_panic_calls_share_reference(tmp_path):
    mod = util.build_ir_mod(
        tmp_path,
        """
        pub fn main() i32 {
            let x: i32 = 1;
            if (x == 0) { panic("never"); };
            return x + 1 - 2;
        }
        """,
    )
    main = next(fn for fn in mod.fns if fn.is_main)
    panic_calls = []
    for bb in main.instantiate(()).cfg.nodes:
        for instr in bb.instrs:
            if not isinstance(instr, ir_values.CallInstr):
                continue
            callee = instr.callee
            if isinstance(callee, ir_module.FnRef) and callee.instance.name == "panic":
                panic_calls.append(instr)

    # There may be multiple synthesized arithmetic checks; require both the
    # source call and at least one synthesized call before comparing identity.
    assert any(call.ast is not None for call in panic_calls)
    assert any(call.ast is None for call in panic_calls)
    panic_callees = [call.callee for call in panic_calls]
    assert all(isinstance(callee, ir_module.FnRef) for callee in panic_callees)
    assert all(callee is panic_callees[0] for callee in panic_callees)
