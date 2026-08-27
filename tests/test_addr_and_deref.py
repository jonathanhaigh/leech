# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_addr_and_deref(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 10;
        let y = &x;
        return y.*;
    }
    """
    util.check_prog_output(tmp_path, src, "", 10)


def test_comptime_addr_and_deref(tmp_path):
    src = """
    let x = 10;
    let y = &x;
    let z = y.*;
    pub fn main() i32 {
        return z;
    }
    """
    util.check_prog_output(tmp_path, src, "", 10)


def test_addr_and_deref_fn(tmp_path):
    src = """
    fn f() i32 { return 10; }
    pub fn main() i32 {
        let x = &f;
        let y = x.*;
        return y();
    }
    """
    with pytest.raises(errors.DerefInvalidTypError):
        util.compile_str(tmp_path, src)


def test_comptime_addr_and_deref_fn(tmp_path):
    src = """
    fn f() i32 { return 10; }
    let x = &f;
    let y = x.*;
    pub fn main() i32 {
        return y();
    }
    """
    with pytest.raises(errors.DerefInvalidTypError):
        util.compile_str(tmp_path, src)


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
    util.check_prog_output(tmp_path, src, "", 11)


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
    util.check_prog_output(tmp_path, src, "", 11)


def test_addr_and_deref_array_elt(tmp_path):
    src = """
    pub fn main() i32 {
        let x = array[i32, 3]{101, 102, 103};
        let y = array[*i32, 3]{&x.[2usize], &x.[1usize], &x.[0usize]};
        return y.[1usize].*;
    }
    """
    util.check_prog_output(tmp_path, src, "", 102)


def test_addr_and_deref_comptime_array_elt(tmp_path):
    src = """
    let x = array[i32, 3]{101, 102, 103};
    let y = array[*i32, 3]{&x.[2usize], &x.[1usize], &x.[0usize]};
    let z = y.[1usize].*;

    pub fn main() i32 {
        return z;
    }
    """
    util.check_prog_output(tmp_path, src, "", 102)


def test_addr_and_deref_nested(tmp_path):
    src = """

    struct T {
        a: array[*i32, 3],
    }

    pub fn main() i32 {
        let w = array[i32, 3]{101, 102, 103};
        let x = T{
            a: array[*i32, 3]{&w.[0usize], &w.[1usize], &w.[2usize]},
        };
        let y = array[*T, 1]{&x};
        let z = &y;
        let ret = z.*.[0usize].*.a.[2usize].*;
        return ret;
    }
    """
    util.check_prog_output(tmp_path, src, "", 103)


def test_addr_and_deref_comptime_nested(tmp_path):
    src = """

    struct T {
        a: array[*i32, 3],
    }

    let w = array[i32, 3]{101, 102, 103};
    let x = T{
        a: array[*i32, 3]{&w.[0usize], &w.[1usize], &w.[2usize]},
    };
    let y = array[*T, 1]{&x};
    let z = &y;
    let ret = z.*.[0usize].*.a.[2usize].*;

    pub fn main() i32 {
        return ret;
    }
    """
    util.check_prog_output(tmp_path, src, "", 103)


def test_addr_of_tmp(tmp_path):
    src = """
    fn deref(ptr: *i32) i32 {
        return ptr.*;
    }

    pub fn main() i32 {
        return deref(&100);
    }
    """
    util.check_prog_output(tmp_path, src, "", 100)


def test_addr_of_comptime_value(tmp_path):
    src = """
    let x = &1;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CannotTakeAddressOfComptimeValueError):
        util.compile_str(tmp_path, src)


def test_addr_of_comptime_addr_of_deref(tmp_path):
    src = """
    let x = 1;
    let y = &x;
    let z = &(y.*);
    pub fn main() i32 {
        return z.*;
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_deref_non_ptr(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 10;
        return x.*;
    }
    """
    with pytest.raises(errors.DerefInvalidTypError):
        util.compile_str(tmp_path, src)


def test_deref_comptime_non_ptr(tmp_path):
    src = """
    let x = 10;
    let y = x.*;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.DerefInvalidTypError):
        util.compile_str(tmp_path, src)


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
    with pytest.raises(errors.CannotTakeAddressOfComptimeValueError):
        util.compile_str(tmp_path, src)


def test_comptime_return_addr_of_local_in_array(tmp_path):
    src = """
    fn f() array[*i32, 1] {
        let a = 1;
        return array[*i32, 1]{&a};
    }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.CannotTakeAddressOfComptimeValueError):
        util.compile_str(tmp_path, src)


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
    with pytest.raises(errors.CannotTakeAddressOfComptimeValueError):
        util.compile_str(tmp_path, src)
