# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import lark
import pytest

from leech import errors, parse


def test_while_stmt_no_semicolon(compiler):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        }
        return i;
    }
    """
    compiler.check(src, exit_status=10)


def test_if_stmt_no_semicolon(compiler):
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
    compiler.check(src, exit_status=5)


def test_block_expr_stmt_no_semicolon(compiler):
    src = """
    pub fn main() i32 {
        {
            let x = 1;
        }
        return 7;
    }
    """
    compiler.check(src, exit_status=7)


def test_match_stmt_no_semicolon_parses():
    tree = parse.build_parser("block_expr").parse("{ match (x) { _ => 1, } y }")

    match_expr, var_expr = tree.children
    assert isinstance(match_expr, lark.Tree)
    assert isinstance(var_expr, lark.Tree)
    assert [match_expr.data, var_expr.data] == ["match_expr", "var_expr"]


def test_match_stmt_with_semicolon_parses():
    tree = parse.build_parser("block_expr").parse("{ match (x) { _ => 1, }; y }")

    stmt = tree.children[0]
    assert isinstance(stmt, lark.Tree)
    assert stmt.data == "stmt"
    expr_stmt = stmt.children[0]
    assert isinstance(expr_stmt, lark.Tree)
    assert expr_stmt.data == "expr_stmt"
    match_expr = expr_stmt.children[0]
    assert isinstance(match_expr, lark.Tree)
    assert match_expr.data == "match_expr"


def test_semicolon_still_required_to_discard_tail_value(compiler):
    src = """
    pub fn main() i32 {
        while (true) {
            if (true) { 1 } else { 2 }
        }
        return 0;
    }
    """
    with pytest.raises(errors.WhileTypNotVoidError):
        compiler.compile(src)


def test_semicolon_discards_tail_value(compiler):
    src = """
    pub fn main() i32 {
        while (true) {
            if (true) { 1 } else { 2 };
            return 0;
        }
        return 1;
    }
    """
    compiler.check(src)
