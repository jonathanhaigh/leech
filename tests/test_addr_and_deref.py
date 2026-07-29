import pytest
from util import check_prog_output, compile_str

from leech.errors import CannotTakeAddressOfComptimeValueError, DerefInvalidTypError


def test_addr_and_deref(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 10;
        let y = &x;
        return y.*;
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_comptime_addr_and_deref(tmp_path):
    src = """
    let x = 10;
    let y = &x;
    let z = y.*;
    pub fn main() i32 {
        return z;
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_addr_and_deref_fn(tmp_path):
    src = """
    fn f() i32 { return 10; }
    pub fn main() i32 {
        let x = &f;
        let y = x.*;
        return y();
    }
    """
    with pytest.raises(DerefInvalidTypError):
        compile_str(tmp_path, src)


def test_comptime_addr_and_deref_fn(tmp_path):
    src = """
    fn f() i32 { return 10; }
    let x = &f;
    let y = x.*;
    pub fn main() i32 {
        return y();
    }
    """
    with pytest.raises(DerefInvalidTypError):
        compile_str(tmp_path, src)


def test_addr_and_deref_field(tmp_path):
    src = """
    struct T {
        a: i32,
    }

    struct U {
        b: *i32,
    }

    pub fn main() i32 {
        let x = T {a: 11};
        let y = U {b: &x.a};
        return y.b.*;
    }
    """
    check_prog_output(tmp_path, src, "", 11)


def test_addr_and_deref_comptime_field(tmp_path):
    src = """
    struct T {
        a: i32,
    }

    struct U {
        b: *i32,
    }

    let x = T {a: 11};
    let y = U {b: &x.a};
    let z = y.b.*;

    pub fn main() i32 {
        return z;
    }
    """
    check_prog_output(tmp_path, src, "", 11)


def test_addr_and_deref_array_elt(tmp_path):
    src = """
    pub fn main() i32 {
        let x = [101, 102, 103];
        let y = [&x.[2usize], &x.[1usize], &x.[0usize]];
        return y.[1usize].*;
    }
    """
    check_prog_output(tmp_path, src, "", 102)


def test_addr_and_deref_comptime_array_elt(tmp_path):
    src = """
    let x = [101, 102, 103];
    let y = [&x.[2usize], &x.[1usize], &x.[0usize]];
    let z = y.[1usize].*;

    pub fn main() i32 {
        return z;
    }
    """
    check_prog_output(tmp_path, src, "", 102)


def test_addr_and_deref_nested(tmp_path):
    src = """

    struct T {
        a: [*i32; 3],
    }

    pub fn main() i32 {
        let w = [101, 102, 103];
        let x = T{
            a: [&w.[0usize], &w.[1usize], &w.[2usize]],
        };
        let y = [&x];
        let z = &y;
        let ret = z.*.[0usize].*.a.[2usize].*;
        return ret;
    }
    """
    check_prog_output(tmp_path, src, "", 103)


def test_addr_and_deref_comptime_nested(tmp_path):
    src = """

    struct T {
        a: [*i32; 3],
    }

    let w = [101, 102, 103];
    let x = T{
        a: [&w.[0usize], &w.[1usize], &w.[2usize]],
    };
    let y = [&x];
    let z = &y;
    let ret = z.*.[0usize].*.a.[2usize].*;

    pub fn main() i32 {
        return ret;
    }
    """
    check_prog_output(tmp_path, src, "", 103)


def test_addr_of_tmp(tmp_path):
    src = """
    fn deref(ptr: *i32) i32 {
        return ptr.*;
    }

    pub fn main() i32 {
        return deref(&100);
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_addr_of_comptime_value(tmp_path):
    src = """
    let x = &1;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CannotTakeAddressOfComptimeValueError):
        compile_str(tmp_path, src)


def test_addr_of_comptime_addr_of_deref(tmp_path):
    src = """
    let x = 1;
    let y = &x;
    let z = &(y.*);
    pub fn main() i32 {
        return z.*;
    }
    """
    check_prog_output(tmp_path, src, "", 1)


def test_deref_non_ptr(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 10;
        return x.*;
    }
    """
    with pytest.raises(DerefInvalidTypError):
        compile_str(tmp_path, src)


def test_deref_comptime_non_ptr(tmp_path):
    src = """
    let x = 10;
    let y = x.*;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(DerefInvalidTypError):
        compile_str(tmp_path, src)


def test_comptime_return_addr_of_local(tmp_path):
    src = """
    fn f() *i32 {
        let a = 1;
        return &a;
    }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CannotTakeAddressOfComptimeValueError):
        compile_str(tmp_path, src)


def test_comptime_return_addr_of_local_in_array(tmp_path):
    src = """
    fn f() [*i32; 1] {
        let a = 1;
        return [&a];
    }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CannotTakeAddressOfComptimeValueError):
        compile_str(tmp_path, src)


def test_comptime_return_addr_of_local_in_struct(tmp_path):
    src = """
    struct T {
        a: *i32,
    }
    fn f() T {
        let b = 1;
        return T{a: &b};
    }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CannotTakeAddressOfComptimeValueError):
        compile_str(tmp_path, src)
