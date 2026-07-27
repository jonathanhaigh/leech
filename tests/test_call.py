import pytest

from leech.leecherrors import (
    CallExternFnAtComptimeError,
    InvalidArgTypError,
    NotCallableError,
    NotEnoughArgsError,
    TooManyArgsError,
)
from util import compile_str


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


def test_comptime_not_callable(tmp_path):
    src = """
    let x = 100;
    let y = x();
    pub fn main() i32 {
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


def test_comptime_invalid_arg_typ(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f("abc");
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(InvalidArgTypError):
        compile_str(tmp_path, src)


def test_void_call_as_arg(tmp_path):
    src = """
    fn f() { }
    fn g(x: i32) i32 { return x; }
    pub fn main() i32 {
        return g(f());
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


def test_comptime_too_many_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f(1, 2);
    pub fn main() i32 {
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


def test_comptime_not_enough_args(tmp_path):
    src = """
    pub fn f(x: i32) i32 { x + x }
    let x = f();
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(NotEnoughArgsError):
        compile_str(tmp_path, src)


def test_comptime_call_extern(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    let x = puts("abc");
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(CallExternFnAtComptimeError):
        compile_str(tmp_path, src)
