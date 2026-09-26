# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors


def test_while(compiler):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        };
        return i;
    }
    """
    compiler.check(src, exit_status=10)


def test_comptime_while(compiler):
    src = """
    let x = {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        };
        i
    };
    pub fn main() i32 {
        return x;
    }
    """
    compiler.check(src, exit_status=10)


def test_if_in_while(compiler):
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
    compiler.check(src, exit_status=128)


def test_while_body_not_void(compiler):
    src = """
    pub fn main() i32 {
        while (true) {
            1
        };
        return 0;
    }
    """
    with pytest.raises(errors.WhileTypNotVoidError):
        compiler.compile(src)


def test_void_call_as_while_cond(compiler):
    src = """
    fn f() { }
    pub fn main() i32 {
        while (f()) {
        };
        return 0;
    }
    """
    with pytest.raises(errors.WhileCondNotBoolError):
        compiler.compile(src)
