# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors
from tests import util


def test_tail_expr_return(tmp_path):
    src = """
    pub fn main() i32 { 100 }
    """
    util.check_prog_output(tmp_path, src, "", 100)


def test_missing_return(compiler):
    src = """
    extern fn puts(s: *u8) i32;
    pub fn main() i32 {
        puts("abcd");
    }
    """
    with pytest.raises(errors.MissingRetError):
        compiler.compile(src)


def test_invalid_void_return(compiler):
    src = """
    pub fn main() i32 {
        return;
    }
    """
    with pytest.raises(errors.InvalidVoidRetError):
        compiler.compile(src)


def test_invalid_return_typ(compiler):
    src = """
    pub fn main() i32 {
        return "abcd";
    }
    """
    with pytest.raises(errors.InvalidRetTypError):
        compiler.compile(src)


def test_return_typ_not_defined(compiler):
    src = """
    pub fn f() not_a_typ { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_comptime_return(compiler):
    src = """
    let x = {
        return 1;
    };
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.RetNotInFnError):
        compiler.compile(src)
