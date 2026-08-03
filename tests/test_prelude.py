# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import signal

import util


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
