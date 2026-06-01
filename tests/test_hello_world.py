import subprocess

import pytest

from l0.main import compile
from l0.l0errors import (
    AssignToConstError,
    DuplicateVarDefnError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    InvalidArgTypError,
    InvalidRetTypError,
    InvalidVoidRetError,
    MissingRetError,
    NotCallableError,
    NotEnoughArgsError,
    TooManyArgsError,
    TypNotFoundError,
    VarNotFoundError,
)


def check_prog_output(src, expected_output, expected_exit_status):
    ir = compile(src)
    proc = subprocess.run(
        ["lli"],
        input=ir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    assert proc.stdout == f"{expected_output}"
    assert proc.returncode == expected_exit_status


def test_hello_world():
    src = """
    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts("hello world");
        return 0;
    }
    """
    check_prog_output(src, "hello world\n", 0)


def test_int_arith():
    src = """
    pub fn main() i32 {
        return 1 - 2 * 3 + 4 - 5 * 6 / 10;
    }
    """
    check_prog_output(src, "", 256 - 4)


def test_tail_expr_return():
    src = """
    pub fn main() i32 { 100 }
    """
    check_prog_output(src, "", 100)


def test_missing_return():
    src = """
    extern fn puts(s: *u8) i32;
    pub fn main() i32 {
        puts("abcd");
    }
    """
    with pytest.raises(MissingRetError):
        compile(src)


def test_invalid_void_return():
    src = """
    pub fn main() i32 {
        return;
    }
    """
    with pytest.raises(InvalidVoidRetError):
        compile(src)


def test_invalid_return_typ():
    src = """
    pub fn main() i32 {
        return "abcd";
    }
    """
    with pytest.raises(InvalidRetTypError):
        compile(src)


def test_return_typ_not_defined():
    src = """
    pub fn f() not_a_typ { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(TypNotFoundError):
        compile(src)


def test_param_typ_not_defined():
    src = """
    pub fn f(p: not_a_typ) { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(TypNotFoundError):
        compile(src)


def test_if_false_else_expr_val():
    src = """
    pub fn main() i32 {
        return if (false) {
            1
        } else {
            2
        };
    }
    """
    check_prog_output(src, "", 2)


def test_if_true_else_expr_val():
    src = """
    pub fn main() i32 {
        return if (true) {
            1
        } else {
            2
        };
    }
    """
    check_prog_output(src, "", 1)


def test_if_false_ret_else_expr_val():
    src = """
    pub fn main() i32 {
        return if (false) {
            return 5;
        } else {
            2
        };
    }
    """
    check_prog_output(src, "", 2)


def test_if_true_ret_else_expr_val():
    src = """
    pub fn main() i32 {
        return if (true) {
            return 5;
        } else {
            2
        };
    }
    """
    check_prog_output(src, "", 5)


def test_if_false_else_ret_expr_val():
    src = """
    pub fn main() i32 {
        return if (false) {
            1
        } else {
            return 5;
        };
    }
    """
    check_prog_output(src, "", 5)


def test_if_true_else_ret_expr_val():
    src = """
    pub fn main() i32 {
        return if (true) {
            1
        } else {
            return 5;
        };
    }
    """
    check_prog_output(src, "", 1)


def test_if_ret_else_ret_expr_val():
    src = """
    pub fn main() i32 {
        if (true) {
            return 1; 
        } else {
            return 2;
        };
    }
    """
    check_prog_output(src, "", 1)


def test_if_ret_expr_val():
    src = """
    pub fn main() i32 {
        if (true) {
            return 1; 
        };
    }
    """
    check_prog_output(src, "", 1)


def test_if_with_tail_expr():
    src = """
    pub fn main() i32 {
        if (true) {
            1 
        };
        return 0;
    }
    """
    with pytest.raises(IfTypNotVoidError):
        compile(src)


def test_if_els_with_mismatching_typs():
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
        compile(src)


def test_int_mod_var_ref_in_fn():
    src = """
    let a = 100;
    pub fn main() i32 {
        return a;
    }
    """
    check_prog_output(src, "", 100)


def test_str_mod_var_ref_in_fn():
    src = """
    let s = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(s);
        return 100;
    }
    """
    check_prog_output(src, "abcd\n", 100)


def test_int_mod_var_ref_in_mod():
    src = """
    let a = 100;
    let b = a;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        return b;
    }
    """
    check_prog_output(src, "", 100)


def test_str_mod_var_ref_in_mod():
    src = """
    let b = a;
    let a = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    check_prog_output(src, "abcd\n", 100)

def test_var_not_found_at_mod_scope():
    src = """
    let a = x;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(VarNotFoundError):
        compile(src)

def test_var_not_found_at_fn_scope():
    src = """
    pub fn main() i32 { x }
    """
    with pytest.raises(VarNotFoundError):
        compile(src)

def test_shadowed_mod_var():
    src = """
    let x = 100;
    pub fn main() i32 {
        let x = 200;
        return x;
    }
    """
    check_prog_output(src, "", 200)

def test_unshadowed_mod_var():
    src = """
    let x = 100;
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(src, "", 100)

def test_shadowed_local_var():
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
            return x;
        };
    }
    """
    check_prog_output(src, "", 200)

def test_unshadowed_local_var():
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(src, "", 100)

def test_cannot_access_inner_scope():
    src = """
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    with pytest.raises(VarNotFoundError):
        compile(src)

def test_duplicate_mod_var():
    src = """
    let x = 100;
    let x = 200;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(DuplicateVarDefnError):
        compile(src)

def test_duplicate_local_var():
    src = """
    pub fn main() i32 {
        let x = 100;
        let x = 200;
        return 0;
    }
    """
    with pytest.raises(DuplicateVarDefnError):
        compile(src)

def test_not_callable():
    src = """
    pub fn main() i32 {
        let x = 100;
        x();
        return 0;
    }
    """
    with pytest.raises(NotCallableError):
        compile(src)

def test_invalid_arg_typ():
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f("abc");
        return 0;
    }
    """
    with pytest.raises(InvalidArgTypError):
        compile(src)

def test_too_many_args():
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f(1, 2);
        return 0;
    }
    """
    with pytest.raises(TooManyArgsError):
        compile(src)

def test_not_enough_args():
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f();
        return 0;
    }
    """
    with pytest.raises(NotEnoughArgsError):
        compile(src)

@pytest.mark.xfail
def test_mod_var_cycle():
    src = """
    let b = a;
    let a = b;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    check_prog_output(src, "abcd\n", 100)


def test_assign_to_local_var():
    src = """
    pub fn main() i32 {
        let mut a = 1;
        a = 2;
        return a;
    }
    """
    check_prog_output(src, "", 2)


def test_assign_to_mod_var():
    src = """
    let mut a = 1;
    pub fn main() i32 {
        a = 2;
        return a;
    }
    """
    check_prog_output(src, "", 2)


def test_assign_to_const_local_var():
    src = """
    pub fn main() i32 {
        let a = 1;
        a = 2;
        return a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile(src)


def test_assign_to_const_mod_var():
    src = """
    let a = 1;
    pub fn main() i32 {
        a = 2;
        return a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile(src)
