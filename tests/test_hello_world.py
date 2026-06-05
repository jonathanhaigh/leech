from pathlib import Path
import subprocess

import pytest

from l0.main import compile
from l0.l0errors import (
    AssignToConstError,
    DuplicateVarDefnError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    IncompatibleBinOpArgTypsError,
    IncompatibleTypInArrayExpr,
    IndexIntoInvalidTypError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    InvalidIndexTypError,
    InvalidRetTypError,
    InvalidVoidRetError,
    MissingRetError,
    NotCallableError,
    NotEnoughArgsError,
    TooManyArgsError,
    TypNotFoundError,
    VarNotFoundError,
)
from l0.src import SrcFile


def compile_str(tmp_path: Path, src: str) -> str:
    path = tmp_path / "main.l0"
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)

    return compile(SrcFile(path))


def check_prog_output(
    tmp_path: Path, src: str, expected_output, expected_exit_status
) -> None:
    ir = compile_str(tmp_path, src)
    proc = subprocess.run(
        ["lli"],
        input=ir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    assert proc.stdout == f"{expected_output}"
    assert proc.returncode == expected_exit_status


def test_hello_world(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts("hello world");
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "hello world\n", 0)


def test_int_arith(tmp_path):
    src = """
    pub fn main() i32 {
        return 1 - 2 * 3 + 4 - 5 * 6 / 10;
    }
    """
    check_prog_output(tmp_path, src, "", 256 - 4)


@pytest.mark.parametrize(
    "op,lhs,rhs,result",
    (
        ("<", 1, 2, True),
        ("<", 2, 1, False),
        ("<", 1, 1, False),
        ("<=", 1, 2, True),
        ("<=", 2, 1, False),
        ("<=", 1, 1, True),
        ("==", 1, 2, False),
        ("==", 2, 1, False),
        ("==", 1, 1, True),
        (">=", 1, 2, False),
        (">=", 2, 1, True),
        (">=", 1, 1, True),
        (">", 1, 2, False),
        (">", 2, 1, True),
        (">", 1, 1, False),
    ),
)
def test_cmp(op, lhs, rhs, result, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if ({lhs} {op} {rhs}) {{ 100 }} else {{ 200 }};
    }}
    """
    expected_status = 100 if result else 200
    check_prog_output(tmp_path, src, "", expected_status)


def test_tail_expr_return(tmp_path):
    src = """
    pub fn main() i32 { 100 }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_missing_return(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    pub fn main() i32 {
        puts("abcd");
    }
    """
    with pytest.raises(MissingRetError):
        compile_str(tmp_path, src)


def test_invalid_void_return(tmp_path):
    src = """
    pub fn main() i32 {
        return;
    }
    """
    with pytest.raises(InvalidVoidRetError):
        compile_str(tmp_path, src)


def test_invalid_return_typ(tmp_path):
    src = """
    pub fn main() i32 {
        return "abcd";
    }
    """
    with pytest.raises(InvalidRetTypError):
        compile_str(tmp_path, src)


def test_return_typ_not_defined(tmp_path):
    src = """
    pub fn f() not_a_typ { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(TypNotFoundError):
        compile_str(tmp_path, src)


def test_param_typ_not_defined(tmp_path):
    src = """
    pub fn f(p: not_a_typ) { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(TypNotFoundError):
        compile_str(tmp_path, src)


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


def test_int_mod_var_ref_in_fn(tmp_path):
    src = """
    let a = 100;
    pub fn main() i32 {
        return a;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_str_mod_var_ref_in_fn(tmp_path):
    src = """
    let s = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(s);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)


def test_int_mod_var_ref_in_mod(tmp_path):
    src = """
    let a = 100;
    let b = a;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        return b;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_str_mod_var_ref_in_mod(tmp_path):
    src = """
    let b = a;
    let a = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)


def test_var_not_found_at_mod_scope(tmp_path):
    src = """
    let a = x;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(VarNotFoundError):
        compile_str(tmp_path, src)


def test_var_not_found_at_fn_scope(tmp_path):
    src = """
    pub fn main() i32 { x }
    """
    with pytest.raises(VarNotFoundError):
        compile_str(tmp_path, src)


def test_shadowed_mod_var(tmp_path):
    src = """
    let x = 100;
    pub fn main() i32 {
        let x = 200;
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 200)


def test_unshadowed_mod_var(tmp_path):
    src = """
    let x = 100;
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_shadowed_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
            return x;
        };
    }
    """
    check_prog_output(tmp_path, src, "", 200)


def test_unshadowed_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_cannot_access_inner_scope(tmp_path):
    src = """
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    with pytest.raises(VarNotFoundError):
        compile_str(tmp_path, src)


def test_duplicate_mod_var(tmp_path):
    src = """
    let x = 100;
    let x = 200;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(DuplicateVarDefnError):
        compile_str(tmp_path, src)


def test_duplicate_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        let x = 200;
        return 0;
    }
    """
    with pytest.raises(DuplicateVarDefnError):
        compile_str(tmp_path, src)


def test_not_callable(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        x();
        return 0;
    }
    """
    with pytest.raises(NotCallableError):
        compile_str(tmp_path, src)


def test_invalid_arg_typ(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f("abc");
        return 0;
    }
    """
    with pytest.raises(InvalidArgTypError):
        compile_str(tmp_path, src)


def test_too_many_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f(1, 2);
        return 0;
    }
    """
    with pytest.raises(TooManyArgsError):
        compile_str(tmp_path, src)


def test_not_enough_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    pub fn main() i32 {
        f();
        return 0;
    }
    """
    with pytest.raises(NotEnoughArgsError):
        compile_str(tmp_path, src)


@pytest.mark.xfail
def test_mod_var_cycle(tmp_path):
    src = """
    let b = a;
    let a = b;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)


def test_assign_to_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let mut a = 1;
        a = 2;
        return a;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_assign_to_mod_var(tmp_path):
    src = """
    let mut a = 1;
    pub fn main() i32 {
        a = 2;
        return a;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_assign_to_const_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let a = 1;
        a = 2;
        return a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_assign_to_const_mod_var(tmp_path):
    src = """
    let a = 1;
    pub fn main() i32 {
        a = 2;
        return a;
    }
    """
    with pytest.raises(AssignToConstError):
        compile_str(tmp_path, src)


def test_while(tmp_path):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        };
        return i;
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_if_in_while(tmp_path):
    src = """
    pub fn main() i32 {
        let mut i = 1;
        while (true) {
            i = 2 * i;
            if (i >= 120) {
                return i;
            }
        };
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 128)


@pytest.mark.parametrize(
    "lhs,rhs",
    (
        ("0u8", "0i8"),
        ("0i10", "0i11"),
        ("0u10", "0u11"),
    ),
)
def test_incompatible_bin_op_args(lhs, rhs, tmp_path):
    src = f"""
    pub fn main() i32 {{
        x = {lhs} + {rhs};
        return 0;
    }}
    """
    with pytest.raises(IncompatibleBinOpArgTypsError):
        compile_str(tmp_path, src)


def test_invalid_bin_op_arg(tmp_path):
    src = """
    pub fn main() i32 {
        x = true + false;
        return 0;
    }
    """
    with pytest.raises(InvalidBinOpArgTypError):
        compile_str(tmp_path, src)


def test_mod_array(tmp_path):
    src = """
    let a = 9;
    let arr = [a, 10, 11, 12];
    pub fn main() i32 {
        let idx = 0usize;
        return arr[idx] + arr[1usize];
    }
    """
    check_prog_output(tmp_path, src, "", 19)


def test_comptime_array_access(tmp_path):
    src = """
    let arr = [1, 2, 3, 4];
    let x = arr[2usize];
    let idx = 3usize;
    let y = arr[idx];
    let z = [10, 20, 30][1usize];

    pub fn main() i32 {
        return x + y + z;
    }
    """
    check_prog_output(tmp_path, src, "", 27)


def test_comptime_array_incompatible_typs(tmp_path):
    src = """
    let arr = [1, 2usize];

    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IncompatibleTypInArrayExpr):
        compile_str(tmp_path, src)


def test_comptime_array_invalid_index(tmp_path):
    src = """
    let arr = [1, 2];
    let x = arr[true];

    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(InvalidIndexTypError):
        compile_str(tmp_path, src)


def test_comptime_index_into_non_array(tmp_path):
    src = """
    let not_an_arr = 1;
    let x = not_an_arr[0usize];

    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IndexIntoInvalidTypError):
        compile_str(tmp_path, src)


def test_local_array(tmp_path):
    src = """
    pub fn main() i32 {
        let a = 5;
        let arr = [1, 2, 3, 4, a];
        let idx = 3usize;
        return arr[idx + 1usize];
    }
    """
    check_prog_output(tmp_path, src, "", 5)


def test_local_array_incompatible_typs(tmp_path):
    src = """

    pub fn main() i32 {
        let x = 1i32;
        let arr = [x, 2usize];
        return 0;
    }
    """
    with pytest.raises(IncompatibleTypInArrayExpr):
        compile_str(tmp_path, src)


def test_local_array_invalid_index(tmp_path):
    src = """

    pub fn main() i32 {
        let arr = [1, 2];
        let idx = true;
        let x = arr[idx];
        return 0;
    }
    """
    with pytest.raises(InvalidIndexTypError):
        compile_str(tmp_path, src)


def test_local_index_into_non_array(tmp_path):
    src = """

    pub fn main() i32 {
        let not_an_arr = 1;
        let x = not_an_arr[0usize];
        return 0;
    }
    """
    with pytest.raises(IndexIntoInvalidTypError):
        compile_str(tmp_path, src)


@pytest.mark.parametrize(
    "typ,value",
    (
        ("bool", "true"),
        ("i32", "1"),
        ("i32", "1i32"),
        ("u17", "10u17"),
        ("usize", "99usize"),
        ("isize", "54isize"),
    ),
)
def test_builtin_typ_lookup(tmp_path, typ, value):
    src = f"""
    fn f() {typ} {{ {value} }}

    pub fn main() i32 {{
        f();
        return 0;
    }}
    """
    check_prog_output(tmp_path, src, "", 0)


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
    check_prog_output(tmp_path, src, "", 120)
