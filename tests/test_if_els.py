# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_if_false_else_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        return if (false) {
            1
        } else {
            2
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 2)


def test_comptime_if_false_else_expr_val(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 2)


def test_if_true_else_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        return if (true) {
            1
        } else {
            2
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_comptime_if_true_else_expr_val(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 1)


def test_if_false_ret_else_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        return if (false) {
            return 5;
        } else {
            2
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 2)


def test_if_true_ret_else_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        return if (true) {
            return 5;
        } else {
            2
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 5)


def test_if_false_else_ret_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        return if (false) {
            1
        } else {
            return 5;
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 5)


def test_if_true_else_ret_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        return if (true) {
            1
        } else {
            return 5;
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_if_ret_else_ret_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        if (true) {
            return 1;
        } else {
            return 2;
        };
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_if_ret_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        if (true) {
            return 1;
        };
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_if_with_tail_expr(tmp_path):
    src = """
    pub fn main() i32 {
        if (true) {
            1
        };
        return 0;
    }
    """
    with pytest.raises(errors.IfTypNotVoidError):
        util.compile_str(tmp_path, src)


def test_if_els_with_mismatching_typs(tmp_path):
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
        util.compile_str(tmp_path, src)


def test_void_call_as_if_cond(tmp_path):
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
        util.compile_str(tmp_path, src)


def test_if_els_two_void_branches(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_if_els_divergent_and_void_branch(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_if_els_two_void_call_arms(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)


def test_if_els_two_void_branches_void_fn(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 0)
