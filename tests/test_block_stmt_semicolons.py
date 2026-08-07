# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_while_stmt_no_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        }
        return i;
    }
    """
    util.check_prog_output(tmp_path, src, "", 10)


def test_if_stmt_no_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        if (true) {
            1
        } else {
            2
        }
        return 5;
    }
    """
    util.check_prog_output(tmp_path, src, "", 5)


def test_block_expr_stmt_no_semicolon(tmp_path):
    src = """
    pub fn main() i32 {
        {
            let x = 1;
        }
        return 7;
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_semicolon_still_required_to_discard_tail_value(tmp_path):
    src = """
    pub fn main() i32 {
        while (true) {
            if (true) { 1 } else { 2 }
        }
        return 0;
    }
    """
    with pytest.raises(errors.WhileTypNotVoidError):
        util.compile_str(tmp_path, src)


def test_semicolon_discards_tail_value(tmp_path):
    src = """
    pub fn main() i32 {
        while (true) {
            if (true) { 1 } else { 2 };
            return 0;
        }
        return 1;
    }
    """
    util.check_prog_output(tmp_path, src, "", 0)
