import pytest

from leech.leecherrors import (
    InvalidRetTypError,
    InvalidVoidRetError,
    MissingRetError,
    ItemNotFoundError,
    RetNotInFnError,
)
from util import check_prog_output, compile_str


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
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_comptime_return(tmp_path):
    src = """
    let x = {
        return 1;
    };
    pub fn main() i32 { 0 }
    """
    with pytest.raises(RetNotInFnError):
        compile_str(tmp_path, src)
