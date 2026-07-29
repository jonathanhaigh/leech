import pytest
from util import check_prog_output, compile_str

from leech.errors import IfCondNotBoolError, IfElsTypMismatchError, IfTypNotVoidError


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
    check_prog_output(tmp_path, src, "", 2)


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
    check_prog_output(tmp_path, src, "", 2)


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
    check_prog_output(tmp_path, src, "", 1)


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
    check_prog_output(tmp_path, src, "", 1)


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
    check_prog_output(tmp_path, src, "", 2)


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
    check_prog_output(tmp_path, src, "", 5)


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
    check_prog_output(tmp_path, src, "", 5)


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
    check_prog_output(tmp_path, src, "", 1)


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
    check_prog_output(tmp_path, src, "", 1)


def test_if_ret_expr_val(tmp_path):
    src = """
    pub fn main() i32 {
        if (true) {
            return 1;
        };
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 1)


def test_if_with_tail_expr(tmp_path):
    src = """
    pub fn main() i32 {
        if (true) {
            1
        };
        return 0;
    }
    """
    with pytest.raises(IfTypNotVoidError):
        compile_str(tmp_path, src)


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
    with pytest.raises(IfElsTypMismatchError):
        compile_str(tmp_path, src)


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
    with pytest.raises(IfCondNotBoolError):
        compile_str(tmp_path, src)
