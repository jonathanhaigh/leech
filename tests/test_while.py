# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


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
    util.check_prog_output(tmp_path, src, "", 10)


def test_comptime_while(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 10)


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
    util.check_prog_output(tmp_path, src, "", 128)


def test_while_body_not_void(tmp_path):
    src = """
    pub fn main() i32 {
        while (true) {
            1
        };
        return 0;
    }
    """
    with pytest.raises(errors.WhileTypNotVoidError):
        util.compile_str(tmp_path, src)


def test_void_call_as_while_cond(tmp_path):
    src = """
    fn f() { }
    pub fn main() i32 {
        while (f()) {
        };
        return 0;
    }
    """
    with pytest.raises(errors.WhileCondNotBoolError):
        util.compile_str(tmp_path, src)
