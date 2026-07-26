import pytest

from l0.l0errors import (
    EmptyArrayTypUnknownError,
    IncompatibleTypInArrayExpr,
    IndexIntoInvalidTypError,
    InvalidArgTypError,
    InvalidIndexTypError,
    VoidArrayElementError,
)

from util import check_prog_output, compile_str


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


def test_array_ret_typ_and_param_typ(tmp_path):
    src = """
    fn f(a: [i32; 4]) [i32; 2] {
        [a[2usize], a[3usize]]
    }

    pub fn main() i32 {
        return f([1, 2, 3, 4])[1usize];
    }
    """
    check_prog_output(tmp_path, src, "", 4)


def test_empty_array_expr_typ_unknown(tmp_path):
    src = """
    pub fn main() i32 {
        let x = [];
        return 0;
    }
    """
    with pytest.raises(EmptyArrayTypUnknownError):
        compile_str(tmp_path, src)


def test_comptime_empty_array_expr_typ_unknown(tmp_path):
    src = """
    let x = [];
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(EmptyArrayTypUnknownError):
        compile_str(tmp_path, src)


def test_empty_array_expr_infers_from_let_typ(tmp_path):
    src = """
    pub fn main() i32 {
        let x: [i32; 0] = [];
        return 42;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_empty_array_as_call_arg(tmp_path):
    src = """
    fn f(a: [i32; 0]) i32 {
        return 42;
    }
    pub fn main() i32 {
        return f([]);
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_comptime_empty_array_as_call_arg(tmp_path):
    src = """
    fn f(a: [i32; 0]) i32 {
        return 42;
    }
    let x = f([]);
    pub fn main() i32 {
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_empty_array_wrong_expected_length(tmp_path):
    src = """
    fn f(a: [i32; 3]) i32 {
        return 0;
    }
    pub fn main() i32 {
        return f([]);
    }
    """
    with pytest.raises(InvalidArgTypError):
        compile_str(tmp_path, src)


def test_empty_array_return(tmp_path):
    src = """
    fn f() [i32; 0] {
        return [];
    }
    pub fn main() i32 {
        let x = f();
        return 42;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_empty_array_struct_field(tmp_path):
    src = """
    struct T {
        arr: [i32; 0],
    }
    pub fn main() i32 {
        let t = T { arr: [] };
        return 55;
    }
    """
    check_prog_output(tmp_path, src, "", 55)


def test_empty_array_assignment(tmp_path):
    src = """
    fn f(a: [i32; 0]) i32 {
        let mut x = a;
        x = [];
        return 99;
    }
    pub fn main() i32 {
        return f([]);
    }
    """
    check_prog_output(tmp_path, src, "", 99)


def test_nested_empty_array_as_call_arg(tmp_path):
    src = """
    fn f(a: [[i32; 0]; 2]) i32 {
        return 7;
    }
    pub fn main() i32 {
        return f([[], []]);
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_void_array_element(tmp_path):
    src = """
    pub fn main() i32 {
        let x = [{}];
        return 0;
    }
    """
    with pytest.raises(VoidArrayElementError):
        compile_str(tmp_path, src)


def test_comptime_void_array_element(tmp_path):
    src = """
    let x = [{}];
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(VoidArrayElementError):
        compile_str(tmp_path, src)


def test_void_array_element_from_later_element(tmp_path):
    src = """
    pub fn main() i32 {
        let x = [1, {}];
        return 0;
    }
    """
    with pytest.raises(IncompatibleTypInArrayExpr):
        compile_str(tmp_path, src)
