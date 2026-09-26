# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0


def test_hello_world(compiler):
    src = """
    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts("hello world");
        return 0;
    }
    """
    compiler.check(src, stdout="hello world\n")


def test_recursive_factorial_fn(compiler):
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
    compiler.check(src, exit_status=120)


def test_comptime_recursive_factorial_fn(compiler):
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
    compiler.check(src, exit_status=120)


def test_mutually_recursive_fns(compiler):
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
    compiler.check(src, exit_status=1)


def test_comptime_mutually_recursive_fns(compiler):
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
    compiler.check(src, exit_status=1)
