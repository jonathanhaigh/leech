import pytest

from l0.l0errors import (
    IncompatibleAssignmentTypError,
    IncompatibleStructFieldTypError,
    InvalidArgTypError,
    InvalidRetTypError,
)
from util import check_prog_output, compile_str


def test_mut_ptr_coerces_to_const_ptr_arg(tmp_path):
    src = """
    fn get(p: *i32) i32 {
        return p.*;
    }
    pub fn main() i32 {
        let mut x = 42;
        return get(&x);
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_return(tmp_path):
    src = """
    fn f(p: *mut i32) *i32 {
        return p;
    }
    pub fn main() i32 {
        let mut x = 42;
        return f(&x).*;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_tail_expr(tmp_path):
    src = """
    fn f(p: *mut i32) *i32 { p }
    pub fn main() i32 {
        let mut x = 42;
        return f(&x).*;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_assignment(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let mut p = &x;
        let mut y = 7;
        p = &y;
        return p.*;
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_mut_ptr_coerces_to_const_ptr_struct_field(tmp_path):
    src = """
    struct Holder { p: *i32 }
    pub fn main() i32 {
        let mut x = 42;
        let h = Holder { p: &x };
        return h.p.*;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_array_element(tmp_path):
    # The element type comes from the parameter, so the elements coerce.
    src = """
    fn first(a: [*i32; 2]) i32 {
        return a[0usize].*;
    }
    pub fn main() i32 {
        let mut x = 42;
        let mut y = 7;
        return first([&x, &y]);
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_const_ptr_does_not_coerce_to_mut_ptr_arg(tmp_path):
    # The reverse is unsound: it would hand out write access that the
    # place never granted.
    src = """
    fn set(p: *mut i32) {
        p.* = 1;
    }
    pub fn main() i32 {
        let x = 42;
        set(&x);
        return 0;
    }
    """
    with pytest.raises(InvalidArgTypError):
        compile_str(tmp_path, src)


def test_const_ptr_does_not_coerce_to_mut_ptr_return(tmp_path):
    src = """
    fn f(p: *i32) *mut i32 {
        return p;
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(InvalidRetTypError):
        compile_str(tmp_path, src)


def test_const_ptr_does_not_coerce_to_mut_ptr_assignment(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 42;
        let mut y = 7;
        let mut p = &y;
        p = &x;
        return 0;
    }
    """
    with pytest.raises(IncompatibleAssignmentTypError):
        compile_str(tmp_path, src)


def test_ptr_coercion_does_not_change_pointee(tmp_path):
    # Only the mutability may differ - the pointee type still has to
    # match exactly.
    src = """
    struct Holder { p: *u8 }
    pub fn main() i32 {
        let mut x = 42i32;
        let h = Holder { p: &x };
        return 0;
    }
    """
    with pytest.raises(IncompatibleStructFieldTypError):
        compile_str(tmp_path, src)


def test_comptime_mut_ptr_coerces_to_const_ptr(tmp_path):
    src = """
    struct Holder { p: *i32 }
    fn f() i32 {
        let mut x = 42;
        let h = Holder { p: &x };
        return h.p.*;
    }
    let y = f();
    pub fn main() i32 {
        return y;
    }
    """
    check_prog_output(tmp_path, src, "", 42)
