# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import util


def test_hello_world(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts("hello world");
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "hello world\n", 0)


def test_recursive_factorial_fn(tmp_path):
    src = """
    fn fact(n: i32) i32 {
        if (n == 1) {
            return 1;
        };
        return n * fact(n - 1);
    }
    pub fn main() i32 {
        return fact(5);
    }
    """
    util.check_prog_output(tmp_path, src, "", 120)


def test_comptime_recursive_factorial_fn(tmp_path):
    src = """
    fn fact(n: i32) i32 {
        if (n == 1) {
            return 1;
        };
        return n * fact(n - 1);
    }
    let x = fact(5);
    pub fn main() i32 {
        return x;
    }
    """
    util.check_prog_output(tmp_path, src, "", 120)


def test_mutually_recursive_fns(tmp_path):
    # is_even is defined before is_odd but calls it, so this also
    # exercises a forward reference between top-level functions.
    src = """
    fn is_even(n: i32) bool {
        if (n == 0) {
            return true;
        };
        return is_odd(n - 1);
    }

    fn is_odd(n: i32) bool {
        if (n == 0) {
            return false;
        };
        return is_even(n - 1);
    }

    pub fn main() i32 {
        if (is_even(10)) {
            return 1;
        } else {
            return 0;
        }
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_comptime_mutually_recursive_fns(tmp_path):
    src = """
    fn is_even(n: i32) bool {
        if (n == 0) {
            return true;
        };
        return is_odd(n - 1);
    }

    fn is_odd(n: i32) bool {
        if (n == 0) {
            return false;
        };
        return is_even(n - 1);
    }

    let x = is_even(10);

    pub fn main() i32 {
        if (x) {
            return 1;
        } else {
            return 0;
        }
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)
