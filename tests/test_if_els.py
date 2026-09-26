# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors


def test_if_false_else_expr_val(compiler):
    src = """
    pub fn main() i32 {
        return if (false) {
            1
        } else {
            2
        };
    }
    """
    compiler.check(src, exit_status=2)


def test_comptime_if_false_else_expr_val(compiler):
    src = """
    let x = if (false) {
        1
    } else {
        2
    };
    pub fn main() i32 {
        return x;
    }
    """
    compiler.check(src, exit_status=2)


def test_if_true_else_expr_val(compiler):
    src = """
    pub fn main() i32 {
        return if (true) {
            1
        } else {
            2
        };
    }
    """
    compiler.check(src, exit_status=1)


def test_comptime_if_true_else_expr_val(compiler):
    src = """
    let x = if (true) {
        1
    } else {
        2
    };
    pub fn main() i32 {
        return x;
    }
    """
    compiler.check(src, exit_status=1)


def test_if_false_ret_else_expr_val(compiler):
    src = """
    pub fn main() i32 {
        return if (false) {
            return 5;
        } else {
            2
        };
    }
    """
    compiler.check(src, exit_status=2)


def test_if_true_ret_else_expr_val(compiler):
    src = """
    pub fn main() i32 {
        return if (true) {
            return 5;
        } else {
            2
        };
    }
    """
    compiler.check(src, exit_status=5)


def test_if_false_else_ret_expr_val(compiler):
    src = """
    pub fn main() i32 {
        return if (false) {
            1
        } else {
            return 5;
        };
    }
    """
    compiler.check(src, exit_status=5)


def test_if_true_else_ret_expr_val(compiler):
    src = """
    pub fn main() i32 {
        return if (true) {
            1
        } else {
            return 5;
        };
    }
    """
    compiler.check(src, exit_status=1)


def test_if_ret_else_ret_expr_val(compiler):
    src = """
    pub fn main() i32 {
        if (true) {
            return 1;
        } else {
            return 2;
        };
    }
    """
    compiler.check(src, exit_status=1)


def test_if_ret_expr_val(compiler):
    src = """
    pub fn main() i32 {
        if (true) {
            return 1;
        };
        return 0;
    }
    """
    compiler.check(src, exit_status=1)


def test_if_with_tail_expr(compiler):
    src = """
    pub fn main() i32 {
        if (true) {
            1
        };
        return 0;
    }
    """
    with pytest.raises(errors.IfTypNotVoidError):
        compiler.compile(src)


def test_if_els_with_mismatching_typs(compiler):
    src = """
    pub fn main() i32 {
        if (true) {
            1
        }
        else {
            "abc"
        };
        return 0;
    }
    """
    with pytest.raises(errors.IfElsTypMismatchError):
        compiler.compile(src)


def test_void_call_as_if_cond(compiler):
    src = """
    fn f() { }
    pub fn main() i32 {
        if (f()) {
            return 1;
        };
        return 0;
    }
    """
    with pytest.raises(errors.IfCondNotBoolError):
        compiler.compile(src)


def test_if_els_two_void_branches(compiler):
    src = """
    pub fn main() i32 {
        if (true) {
            let x = 1;
        } else {
            let y = 2;
        };
        return 0;
    }
    """
    compiler.check(src)


def test_if_els_divergent_and_void_branch(compiler):
    src = """
    pub fn main() i32 {
        if (true) {
            return 0;
        } else {
            let y = 2;
        };
        return 1;
    }
    """
    compiler.check(src)


def test_if_els_two_void_call_arms(compiler):
    src = """
    fn a() { }
    fn b() { }
    pub fn main() i32 {
        let c = true;
        if (c) {
            a()
        } else {
            b()
        };
        return 0;
    }
    """
    compiler.check(src)


def test_if_els_two_void_branches_void_fn(compiler):
    src = """
    fn f(c: bool) {
        if (c) {
            let x = 1;
        } else {
            let y = 2;
        }
    }
    pub fn main() i32 {
        f(true);
        f(false);
        return 0;
    }
    """
    compiler.check(src)
