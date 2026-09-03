# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import dataclasses
import difflib
from typing import Optional

import lark
import pytest

from leech import parse


@dataclasses.dataclass
class Tok:
    value: Optional[str]


class T(lark.Tree):
    def __init__(self, data, *descendants):
        super().__init__(data, [], None)
        self.leaf = self
        for d in descendants:
            assert self.leaf is not None
            if isinstance(d, Tok):
                self.leaf.children.append(d.value)
                self.leaf = None
            else:
                new_leaf = T(d)
                self.leaf.children.append(new_leaf)
                self.leaf = new_leaf
        self._update_path_shape()

    def cs(self, *children):
        assert self.leaf is not None
        self.leaf.children.extend(children)
        self.leaf = None
        self._update_path_shape()
        return self

    def _update_path_shape(self):
        if self.data == "path":
            self.children = [
                T("path_seg").cs(child, None)
                if isinstance(child, lark.Tree) and child.data == "ident"
                else child
                for child in self.children
            ]
            return
        if self.data not in {"basic_typ", "var_expr"} or len(self.children) != 2:
            return
        path, comptime_args = self.children
        if not isinstance(path, lark.Tree) or path.data != "path":
            return
        last_seg = path.children[-1]
        assert isinstance(last_seg, lark.Tree) and last_seg.data == "path_seg"
        last_seg.children[-1] = comptime_args
        self.children = [path]


def check_parse(rule, src, expected):
    p = parse.build_parser(rule)
    tree = p.parse(src)
    _update_expected_path_shapes(expected)

    if tree != expected:
        got_str = tree.pretty()
        expected_str = expected.pretty()

        print("GOT:\n")
        print(got_str)

        print("EXPECTED:\n")
        print(expected_str)

        print("DIFF:\n")
        diff = difflib.ndiff(
            got_str.splitlines(keepends=True),
            expected_str.splitlines(keepends=True),
        )
        print("".join(diff))

        assert tree == expected


def _update_expected_path_shapes(tree):
    for child in tree.children:
        if isinstance(child, lark.Tree):
            _update_expected_path_shapes(child)
    if isinstance(tree, T):
        tree._update_path_shape()


def check_parse_fails(rule, src):
    p = parse.build_parser(rule)
    with pytest.raises((lark.UnexpectedToken, lark.UnexpectedCharacters)):
        tree = p.parse(src)
        print(tree)


def test_comptime_args_attach_to_each_path_seg():
    check_parse(
        "expr",
        "Option[i32]::Some[bool]",
        T("var_expr").cs(
            T("path").cs(
                T("path_seg").cs(
                    T("ident", Tok("Option")),
                    T("comptime_args").cs(T("basic_typ").cs(T("path", "ident", Tok("i32")), None)),
                ),
                T("path_seg").cs(
                    T("ident", Tok("Some")),
                    T("comptime_args").cs(T("basic_typ").cs(T("path", "ident", Tok("bool")), None)),
                ),
            )
        ),
    )
    check_parse(
        "path_pattern",
        "Option[i32]::Some",
        T("path_pattern").cs(
            T("path").cs(
                T("path_seg").cs(
                    T("ident", Tok("Option")),
                    T("comptime_args").cs(T("basic_typ").cs(T("path", "ident", Tok("i32")), None)),
                ),
                T("path_seg").cs(T("ident", Tok("Some")), None),
            )
        ),
    )


INT_LITS = [
    " 0",
    "1 ",
    " 2 ",
    "3u8",
    "4i32",
    "5i1",
    "6u1",
    "7i9999999999999999999999999",
    "8",
    "9",
    "10",
    "9999999999u14",
    "123456789101112131415161718192021222324252627282930",
]
NOT_INT_LITS = ["01", "()", "a", "", "!", "1 2", "1_2", "1i0", "1u0"]
STR_LITS = [' ""', '"abc" ', ' "0" ', r'"\""', r'"\\"']
NOT_STR_LITS = ["0", '"abc', 'abc"', r'"\"']
IDENTS = [
    "_",
    "a",
    "A",
    "_0",
    "abc_0de",
    " zZ",
    "v10_ ",
    # Merely starting with (or containing) a keyword must still lex as a
    # plain identifier - maximal munch, not keyword-prefix matching.
    "android",
    "nothing",
    "orange",
]
NOT_IDENTS = ["", '"abc"', "0", "9", "1a", "a b", "0_", "("]
COMMENTED_INTS = [
    "// leading comment\n8",
    "8 // trailing comment",
    "// a\n// b\n8 // c",
    "8// no space before comment",
]


@pytest.mark.parametrize(
    "rule,src,expected",
    [("expr", src, T("int_lit", Tok(src.strip()))) for src in INT_LITS]
    + [("expr", src, T("int_lit", Tok("8"))) for src in COMMENTED_INTS]
    + [("expr", src, T("str_lit", Tok(src.strip()))) for src in STR_LITS]
    + [
        ("expr", src, T("var_expr").cs(T("path", "ident", Tok(src.strip())), None))
        for src in IDENTS
    ]
    + [
        (
            "expr",
            "a::b",
            T("var_expr").cs(
                T("path").cs(T("ident", Tok("a")), T("ident", Tok("b"))),
                None,
            ),
        ),
        (
            "expr",
            "_::b10::I",
            T("var_expr").cs(
                T("path").cs(
                    T("ident", Tok("_")),
                    T("ident", Tok("b10")),
                    T("ident", Tok("I")),
                ),
                None,
            ),
        ),
        ("expr", "(1)", T("int_lit", Tok("1"))),
        ("expr", '("x")', T("str_lit", Tok('"x"'))),
        ("expr", "(x)", T("var_expr").cs(T("path", "ident", Tok("x")), None)),
        (
            "expr",
            'abc(123, "xyz", abc_def, f())',
            T("call_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("abc")), None),
                T("arg_list").cs(
                    T("int_lit", Tok("123")),
                    T("str_lit", Tok('"xyz"')),
                    T("var_expr").cs(T("path", "ident", Tok("abc_def")), None),
                    T("call_expr").cs(
                        T("var_expr").cs(T("path", "ident", Tok("f")), None), T("arg_list")
                    ),
                ),
            ),
        ),
        (
            "expr",
            "abc()",
            T("call_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("abc")), None), T("arg_list")
            ),
        ),
        (
            "expr",
            "_(0,)",
            T("call_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("_")), None),
                T("arg_list", "int_lit", Tok("0")),
            ),
        ),
        (
            "expr",
            "array[i32, 4]{1, 2, a, b}",
            T("brace_expr").cs(
                T("basic_typ").cs(
                    T("path", "ident", Tok("array")),
                    T("comptime_args").cs(
                        T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                        T("int_lit", Tok("4")),
                    ),
                ),
                T("brace_element_list").cs(
                    T("int_lit", Tok("1")),
                    T("int_lit", Tok("2")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                    T("var_expr").cs(T("path", "ident", Tok("b")), None),
                ),
            ),
        ),
        (
            "expr",
            "array[i32, 0]{}",
            T("brace_expr").cs(
                T("basic_typ").cs(
                    T("path", "ident", Tok("array")),
                    T("comptime_args").cs(
                        T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                        T("int_lit", Tok("0")),
                    ),
                ),
                T("brace_element_list"),
            ),
        ),
        (
            "expr",
            "a.[0]",
            T("array_access_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
                T("int_lit", Tok("0")),
            ),
        ),
        (
            "expr",
            "f().[x]",
            T("array_access_expr").cs(
                T("call_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("f")), None),
                    T("arg_list"),
                ),
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
            ),
        ),
        (
            "expr",
            "s { a: 10, b: x, c: f(), }",
            T("brace_expr").cs(
                T("basic_typ").cs(T("path", "ident", Tok("s")), None),
                T("brace_element_list").cs(
                    T("struct_field_expr").cs(
                        T("ident", Tok("a")),
                        T("int_lit", Tok("10")),
                    ),
                    T("struct_field_expr").cs(
                        T("ident", Tok("b")),
                        T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    ),
                    T("struct_field_expr").cs(
                        T("ident", Tok("c")),
                        T("call_expr").cs(
                            T("var_expr").cs(T("path", "ident", Tok("f")), None),
                            T("arg_list"),
                        ),
                    ),
                ),
            ),
        ),
        (
            "expr",
            "_empty_struct { }",
            T("brace_expr").cs(
                T("basic_typ").cs(T("path", "ident", Tok("_empty_struct")), None),
                T("brace_element_list"),
            ),
        ),
        (
            "expr",
            "a.b",
            T("field_access_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
                T("ident", Tok("b")),
            ),
        ),
        (
            "expr",
            "a.b.c",
            T("field_access_expr").cs(
                T("field_access_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                    T("ident", Tok("b")),
                ),
                T("ident", Tok("c")),
            ),
        ),
        (
            "expr",
            "arr.[10].xyz",
            T("field_access_expr").cs(
                T("array_access_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("arr")), None),
                    T("int_lit", Tok("10")),
                ),
                T("ident", Tok("xyz")),
            ),
        ),
        (
            "expr",
            "1 + 2",
            T("arith_expr").cs(
                T("int_lit", Tok("1")), T("add_op", Tok("+")), T("int_lit", Tok("2"))
            ),
        ),
        (
            "expr",
            "1 + 2 - 3",
            T("arith_expr").cs(
                T("arith_expr").cs(
                    T("int_lit", Tok("1")),
                    T("add_op", Tok("+")),
                    T("int_lit", Tok("2")),
                ),
                T("add_op", Tok("-")),
                T("int_lit", Tok("3")),
            ),
        ),
        (
            "expr",
            "1 - 4 * 9 / 2 + (7 - 3)",
            T("arith_expr").cs(
                T("arith_expr").cs(
                    T("int_lit", Tok("1")),
                    T("add_op", Tok("-")),
                    T("term").cs(
                        T("term").cs(
                            T("int_lit", Tok("4")),
                            T("mul_op", Tok("*")),
                            T("int_lit", Tok("9")),
                        ),
                        T("mul_op", Tok("/")),
                        T("int_lit", Tok("2")),
                    ),
                ),
                T("add_op", Tok("+")),
                T("arith_expr").cs(
                    T("int_lit", Tok("7")),
                    T("add_op", Tok("-")),
                    T("int_lit", Tok("3")),
                ),
            ),
        ),
        (
            "expr",
            "(a + f())",
            T("arith_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
                T("add_op", Tok("+")),
                T("call_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("f")), None), T("arg_list")
                ),
            ),
        ),
        (
            "expr",
            "a < b",
            T("cmp_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
                T("cmp_op", Tok("<")),
                T("var_expr").cs(T("path", "ident", Tok("b")), None),
            ),
        ),
        (
            "expr",
            "a <= b + 1",
            T("cmp_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
                T("cmp_op", Tok("<=")),
                T("arith_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("b")), None),
                    T("add_op", Tok("+")),
                    T("int_lit", Tok("1")),
                ),
            ),
        ),
        (
            "expr",
            "a + 1 == b",
            T("cmp_expr").cs(
                T("arith_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                    T("add_op", Tok("+")),
                    T("int_lit", Tok("1")),
                ),
                T("cmp_op", Tok("==")),
                T("var_expr").cs(T("path", "ident", Tok("b")), None),
            ),
        ),
        (
            "expr",
            "a != b",
            T("cmp_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
                T("cmp_op", Tok("!=")),
                T("var_expr").cs(T("path", "ident", Tok("b")), None),
            ),
        ),
        (
            "expr",
            "a * 1 - 2 >= b",
            T("cmp_expr").cs(
                T("arith_expr").cs(
                    T("term").cs(
                        T("var_expr").cs(T("path", "ident", Tok("a")), None),
                        T("mul_op", Tok("*")),
                        T("int_lit", Tok("1")),
                    ),
                    T("add_op", Tok("-")),
                    T("int_lit", Tok("2")),
                ),
                T("cmp_op", Tok(">=")),
                T("var_expr").cs(T("path", "ident", Tok("b")), None),
            ),
        ),
        (
            "expr",
            "1 > 2",
            T("cmp_expr").cs(
                T("int_lit", Tok("1")),
                T("cmp_op", Tok(">")),
                T("int_lit", Tok("2")),
            ),
        ),
        (
            "expr",
            "&a",
            T("factor").cs(
                T("unary_op", Tok("&")),
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
            ),
        ),
        (
            "expr",
            "&a * 1",
            T("term").cs(
                T("factor").cs(
                    T("unary_op", Tok("&")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                ),
                T("mul_op", Tok("*")),
                T("int_lit", Tok("1")),
            ),
        ),
        (
            "expr",
            "&&a",
            T("factor").cs(
                T("unary_op", Tok("&")),
                T("factor").cs(
                    T("unary_op", Tok("&")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                ),
            ),
        ),
        (
            "expr",
            "&a.b",
            T("factor").cs(
                T("unary_op", Tok("&")),
                T("field_access_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                    T("ident", Tok("b")),
                ),
            ),
        ),
        (
            "expr",
            "&a.*",
            T("factor").cs(
                T("unary_op", Tok("&")),
                T("deref_expr").cs(T("var_expr").cs(T("path", "ident", Tok("a")), None)),
            ),
        ),
        (
            "expr",
            "a.*.b",
            T("field_access_expr").cs(
                T("deref_expr").cs(T("var_expr").cs(T("path", "ident", Tok("a")), None)),
                T("ident", Tok("b")),
            ),
        ),
        (
            "expr",
            "-a",
            T("factor").cs(
                T("unary_op", Tok("-")),
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
            ),
        ),
        (
            "expr",
            "-a - b",
            T("arith_expr").cs(
                T("factor").cs(
                    T("unary_op", Tok("-")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                ),
                T("add_op", Tok("-")),
                T("var_expr").cs(T("path", "ident", Tok("b")), None),
            ),
        ),
        (
            "expr",
            "- -a",
            T("factor").cs(
                T("unary_op", Tok("-")),
                T("factor").cs(
                    T("unary_op", Tok("-")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                ),
            ),
        ),
        (
            "expr",
            "-a * b",
            T("term").cs(
                T("factor").cs(
                    T("unary_op", Tok("-")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                ),
                T("mul_op", Tok("*")),
                T("var_expr").cs(T("path", "ident", Tok("b")), None),
            ),
        ),
        (
            "expr",
            "-a.b",
            T("factor").cs(
                T("unary_op", Tok("-")),
                T("field_access_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                    T("ident", Tok("b")),
                ),
            ),
        ),
        (
            "expr",
            "a or b and c",
            T("or_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("a")), None),
                T("or_op", Tok("or")),
                T("and_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("b")), None),
                    T("and_op", Tok("and")),
                    T("var_expr").cs(T("path", "ident", Tok("c")), None),
                ),
            ),
        ),
        (
            "expr",
            "not a and b",
            T("and_expr").cs(
                T("not_expr").cs(
                    T("not_op", Tok("not")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                ),
                T("and_op", Tok("and")),
                T("var_expr").cs(T("path", "ident", Tok("b")), None),
            ),
        ),
        (
            "expr",
            "not a == b",
            T("not_expr").cs(
                T("not_op", Tok("not")),
                T("cmp_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                    T("cmp_op", Tok("==")),
                    T("var_expr").cs(T("path", "ident", Tok("b")), None),
                ),
            ),
        ),
        (
            "expr",
            "a or b or c",
            T("or_expr").cs(
                T("or_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                    T("or_op", Tok("or")),
                    T("var_expr").cs(T("path", "ident", Tok("b")), None),
                ),
                T("or_op", Tok("or")),
                T("var_expr").cs(T("path", "ident", Tok("c")), None),
            ),
        ),
        (
            "expr",
            "not not a",
            T("not_expr").cs(
                T("not_op", Tok("not")),
                T("not_expr").cs(
                    T("not_op", Tok("not")),
                    T("var_expr").cs(T("path", "ident", Tok("a")), None),
                ),
            ),
        ),
        (
            "stmt",
            "let x = 1;",
            T("stmt", "let_stmt").cs(
                T("mut", Tok(None)),
                T("ident", Tok("x")),
                None,
                T("int_lit", Tok("1")),
            ),
        ),
        (
            "stmt",
            "let mut x = 10;",
            T("stmt", "let_stmt").cs(
                T("mut", Tok("mut")),
                T("ident", Tok("x")),
                None,
                T("int_lit", Tok("10")),
            ),
        ),
        (
            "stmt",
            "let x: i64 = 1;",
            T("stmt", "let_stmt").cs(
                T("mut", Tok(None)),
                T("ident", Tok("x")),
                T("basic_typ").cs(T("path", "ident", Tok("i64")), None),
                T("int_lit", Tok("1")),
            ),
        ),
        (
            "stmt",
            "let mut x: i64 = -10;",
            T("stmt", "let_stmt").cs(
                T("mut", Tok("mut")),
                T("ident", Tok("x")),
                T("basic_typ").cs(T("path", "ident", Tok("i64")), None),
                T("factor").cs(
                    T("unary_op", Tok("-")),
                    T("int_lit", Tok("10")),
                ),
            ),
        ),
        ("stmt", "return;", T("stmt", "ret_stmt", Tok(None))),
        (
            "stmt",
            'return "abc";',
            T("stmt", "ret_stmt", "str_lit", Tok('"abc"')),
        ),
        ("stmt", "break;", T("stmt", "break_stmt", Tok(None))),
        ("stmt", "break lbl;", T("stmt", "break_stmt", "ident", Tok("lbl"))),
        ("stmt", "continue;", T("stmt", "continue_stmt", Tok(None))),
        (
            "stmt",
            "continue lbl;",
            T("stmt", "continue_stmt", "ident", Tok("lbl")),
        ),
        (
            "expr",
            "while (true) {}",
            T("while_expr").cs(None, T("bool_lit", Tok("true")), T("block_expr")),
        ),
        (
            "expr",
            "lbl: while (true) {}",
            T("while_expr").cs(T("ident", Tok("lbl")), T("bool_lit", Tok("true")), T("block_expr")),
        ),
        (
            "expr",
            "match (x) { _ => 1 }",
            T("match_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("match_arm_list").cs(
                    T("match_arm").cs(T("wildcard_pattern"), T("int_lit", Tok("1")))
                ),
            ),
        ),
        (
            "expr",
            "match (x) { A | B => 1, let mut y => y, -2 => 3, }",
            T("match_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("match_arm_list").cs(
                    T("match_arm").cs(
                        T("or_pattern").cs(
                            T("path_pattern", "path", "ident", Tok("A")),
                            T("path_pattern", "path", "ident", Tok("B")),
                        ),
                        T("int_lit", Tok("1")),
                    ),
                    T("match_arm").cs(
                        T("binding_pattern").cs(T("mut", Tok("mut")), T("ident", Tok("y"))),
                        T("var_expr").cs(T("path", "ident", Tok("y")), None),
                    ),
                    T("match_arm").cs(
                        T("int_lit_pattern").cs("-", T("int_lit", Tok("2"))),
                        T("int_lit", Tok("3")),
                    ),
                ),
            ),
        ),
        (
            "expr",
            "match (x) {}",
            T("match_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("match_arm_list"),
            ),
        ),
        (
            "expr",
            "f(match (x) { true => 1, false => 0, })",
            T("call_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("f")), None),
                T("arg_list", "match_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("match_arm_list").cs(
                        T("match_arm").cs(T("bool_lit", Tok("true")), T("int_lit", Tok("1"))),
                        T("match_arm").cs(T("bool_lit", Tok("false")), T("int_lit", Tok("0"))),
                    ),
                ),
            ),
        ),
        (
            "stmt",
            "x = y;",
            T("stmt", "assignment_stmt").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "stmt",
            "x.[10] = y;",
            T("stmt", "assignment_stmt").cs(
                T("array_access_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("int_lit", Tok("10")),
                ),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "stmt",
            "f().[1] = 2;",
            T("stmt", "assignment_stmt").cs(
                T("array_access_expr").cs(
                    T("call_expr").cs(
                        T("var_expr").cs(T("path", "ident", Tok("f")), None), T("arg_list")
                    ),
                    T("int_lit", Tok("1")),
                ),
                T("int_lit", Tok("2")),
            ),
        ),
        (
            "stmt",
            "x.y = z;",
            T("stmt", "assignment_stmt").cs(
                T("field_access_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("ident", Tok("y")),
                ),
                T("var_expr").cs(T("path", "ident", Tok("z")), None),
            ),
        ),
        (
            "stmt",
            "x.* = y;",
            T("stmt", "assignment_stmt").cs(
                T("deref_expr").cs(T("var_expr").cs(T("path", "ident", Tok("x")), None)),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "stmt",
            "abc();",
            T("stmt", "expr_stmt", "call_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("abc")), None), T("arg_list")
            ),
        ),
        (
            "expr",
            "{ f(0); return 1; }",
            T("block_expr").cs(
                T("stmt", "expr_stmt", "call_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("f")), None),
                    T("arg_list", "int_lit", Tok("0")),
                ),
                T("stmt", "ret_stmt", "int_lit", Tok("1")),
            ),
        ),
        ("expr", "{}", T("block_expr")),
        (
            "expr",
            "{ if (x) { 1 } y }",
            T("block_expr").cs(
                T("if_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("block_expr", "int_lit", Tok("1")),
                ),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "expr",
            "{ if (x) { 1 }; y }",
            T("block_expr").cs(
                T("stmt", "expr_stmt", "if_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("block_expr", "int_lit", Tok("1")),
                ),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "expr",
            "{ while (x) { 1 } y }",
            T("block_expr").cs(
                T("while_expr").cs(
                    None,
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("block_expr", "int_lit", Tok("1")),
                ),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "expr",
            "{ while (x) { 1 }; y }",
            T("block_expr").cs(
                T("stmt", "expr_stmt", "while_expr").cs(
                    None,
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("block_expr", "int_lit", Tok("1")),
                ),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "expr",
            "{ { 1; } y }",
            T("block_expr").cs(
                T("block_expr", "stmt", "expr_stmt", "int_lit", Tok("1")),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "expr",
            "{ { 1; }; y }",
            T("block_expr").cs(
                T("stmt", "expr_stmt", "block_expr", "stmt", "expr_stmt", "int_lit", Tok("1")),
                T("var_expr").cs(T("path", "ident", Tok("y")), None),
            ),
        ),
        (
            "expr",
            "{ if (x) { 1 } while (y) { 2 } z }",
            T("block_expr").cs(
                T("if_expr").cs(
                    T("var_expr").cs(T("path", "ident", Tok("x")), None),
                    T("block_expr", "int_lit", Tok("1")),
                ),
                T("while_expr").cs(
                    None,
                    T("var_expr").cs(T("path", "ident", Tok("y")), None),
                    T("block_expr", "int_lit", Tok("2")),
                ),
                T("var_expr").cs(T("path", "ident", Tok("z")), None),
            ),
        ),
        (
            "expr",
            "{ if (x) { 1 } }",
            T("block_expr", "if_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("block_expr", "int_lit", Tok("1")),
            ),
        ),
        (
            "expr",
            "{ if (x) { 1 }; }",
            T("block_expr", "stmt", "expr_stmt", "if_expr").cs(
                T("var_expr").cs(T("path", "ident", Tok("x")), None),
                T("block_expr", "int_lit", Tok("1")),
            ),
        ),
        ("typ", "i32", T("basic_typ").cs(T("path", "ident", Tok("i32")), None)),
        (
            "typ",
            "a::b::c",
            T("basic_typ").cs(
                T("path").cs(
                    T("ident", Tok("a")),
                    T("ident", Tok("b")),
                    T("ident", Tok("c")),
                ),
                None,
            ),
        ),
        ("typ", "x", T("basic_typ").cs(T("path", "ident", Tok("x")), None)),
        ("typ", "_", T("basic_typ").cs(T("path", "ident", Tok("_")), None)),
        (
            "typ",
            "*u8",
            T("ptr_typ").cs(
                T("mut", Tok(None)), T("basic_typ").cs(T("path", "ident", Tok("u8")), None)
            ),
        ),
        (
            "typ",
            "*mut u8",
            T("ptr_typ").cs(
                T("mut", Tok("mut")), T("basic_typ").cs(T("path", "ident", Tok("u8")), None)
            ),
        ),
        (
            "typ",
            "array[a, 10]",
            T("basic_typ").cs(
                T("path", "ident", Tok("array")),
                T("comptime_args").cs(
                    T("basic_typ").cs(T("path", "ident", Tok("a")), None),
                    T("int_lit", Tok("10")),
                ),
            ),
        ),
        (
            "typ",
            "array[**u8, 0]",
            T("basic_typ").cs(
                T("path", "ident", Tok("array")),
                T("comptime_args").cs(
                    T("ptr_typ").cs(
                        T("mut", Tok(None)),
                        T("ptr_typ").cs(
                            T("mut", Tok(None)),
                            T("basic_typ").cs(T("path", "ident", Tok("u8")), None),
                        ),
                    ),
                    T("int_lit", Tok("0")),
                ),
            ),
        ),
        (
            "typ",
            "array[array[u8, 10], 5]",
            T("basic_typ").cs(
                T("path", "ident", Tok("array")),
                T("comptime_args").cs(
                    T("basic_typ").cs(
                        T("path", "ident", Tok("array")),
                        T("comptime_args").cs(
                            T("basic_typ").cs(T("path", "ident", Tok("u8")), None),
                            T("int_lit", Tok("10")),
                        ),
                    ),
                    T("int_lit", Tok("5")),
                ),
            ),
        ),
        (
            "typ",
            "*array[u8, 1]",
            T("ptr_typ").cs(
                T("mut", Tok(None)),
                T("basic_typ").cs(
                    T("path", "ident", Tok("array")),
                    T("comptime_args").cs(
                        T("basic_typ").cs(T("path", "ident", Tok("u8")), None),
                        T("int_lit", Tok("1")),
                    ),
                ),
            ),
        ),
        (
            "typ",
            "array[i32, N]",
            T("basic_typ").cs(
                T("path", "ident", Tok("array")),
                T("comptime_args").cs(
                    T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    T("basic_typ").cs(T("path", "ident", Tok("N")), None),
                ),
            ),
        ),
        (
            "typ",
            "array[i32, 4]",
            T("basic_typ").cs(
                T("path", "ident", Tok("array")),
                T("comptime_args").cs(
                    T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    T("int_lit", Tok("4")),
                ),
            ),
        ),
        (
            "param",
            "x: i32",
            T("param").cs(
                T("ident", Tok("x")), T("basic_typ").cs(T("path", "ident", Tok("i32")), None)
            ),
        ),
        (
            "param_list",
            "*self",
            T("param_list", "receiver", "mut", Tok(None)),
        ),
        (
            "param_list",
            "*mut self",
            T("param_list", "receiver", "mut", Tok("mut")),
        ),
        (
            "param_list",
            "*self, x: i32",
            T("param_list").cs(
                T("receiver", "mut", Tok(None)),
                T("param").cs(
                    T("ident", Tok("x")), T("basic_typ").cs(T("path", "ident", Tok("i32")), None)
                ),
            ),
        ),
        (
            # The literal "self" token only takes priority over IDENT in
            # the exact position it's grammatically valid (right after
            # "* mut" in a receiver) - everywhere else, including as an
            # ordinary param name, "self" still lexes as IDENT.
            "param",
            "self: i32",
            T("param").cs(
                T("ident", Tok("self")), T("basic_typ").cs(T("path", "ident", Tok("i32")), None)
            ),
        ),
        (
            "param",
            "x :i32",
            T("param").cs(
                T("ident", Tok("x")), T("basic_typ").cs(T("path", "ident", Tok("i32")), None)
            ),
        ),
        (
            "param",
            "x : i32",
            T("param").cs(
                T("ident", Tok("x")), T("basic_typ").cs(T("path", "ident", Tok("i32")), None)
            ),
        ),
        (
            "defn",
            "fn f() {}",
            T("defn", "fn_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("f")),
                None,
                T("param_list"),
                None,
                T("block_expr"),
            ),
        ),
        (
            "defn",
            "fn f() i32 {}",
            T("defn", "fn_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("f")),
                None,
                T("param_list"),
                T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                T("block_expr"),
            ),
        ),
        (
            "defn",
            "pub fn ab_cd(x: a, y: b,) _rt { return 9; }",
            T("defn", "fn_defn").cs(
                T("access", Tok("pub")),
                T("ident", Tok("ab_cd")),
                None,
                T("param_list").cs(
                    T("param").cs(
                        T("ident", Tok("x")), T("basic_typ").cs(T("path", "ident", Tok("a")), None)
                    ),
                    T("param").cs(
                        T("ident", Tok("y")), T("basic_typ").cs(T("path", "ident", Tok("b")), None)
                    ),
                ),
                T("basic_typ").cs(T("path", "ident", Tok("_rt")), None),
                T("block_expr", "stmt", "ret_stmt", "int_lit", Tok("9")),
            ),
        ),
        (
            "defn",
            "struct AStruct {a: i32, pub mut b: *u8, c: AnotherStruct}",
            T("defn", "struct_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("AStruct")),
                None,
                T("struct_field_defn_list").cs(
                    T("struct_field_defn").cs(
                        T("access", Tok(None)),
                        T("mut", Tok(None)),
                        T("ident", Tok("a")),
                        T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    ),
                    T("struct_field_defn").cs(
                        T("access", Tok("pub")),
                        T("mut", Tok("mut")),
                        T("ident", Tok("b")),
                        T("ptr_typ").cs(
                            T("mut", Tok(None)),
                            T("basic_typ").cs(T("path", "ident", Tok("u8")), None),
                        ),
                    ),
                    T("struct_field_defn").cs(
                        T("access", Tok(None)),
                        T("mut", Tok(None)),
                        T("ident", Tok("c")),
                        T("basic_typ").cs(T("path", "ident", Tok("AnotherStruct")), None),
                    ),
                ),
            ),
        ),
        (
            "defn",
            "pub struct EmptyStruct {}",
            T("defn", "struct_defn").cs(
                T("access", Tok("pub")),
                T("ident", Tok("EmptyStruct")),
                None,
                T("struct_field_defn_list"),
            ),
        ),
        (
            "defn",
            "struct Pair[A, B] { first: A, second: B }",
            T("defn", "struct_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("Pair")),
                T("comptime_params").cs(
                    T("comptime_param", "ident", Tok("A")),
                    T("comptime_param", "ident", Tok("B")),
                ),
                T("struct_field_defn_list").cs(
                    T("struct_field_defn").cs(
                        T("access", Tok(None)),
                        T("mut", Tok(None)),
                        T("ident", Tok("first")),
                        T("basic_typ").cs(T("path", "ident", Tok("A")), None),
                    ),
                    T("struct_field_defn").cs(
                        T("access", Tok(None)),
                        T("mut", Tok(None)),
                        T("ident", Tok("second")),
                        T("basic_typ").cs(T("path", "ident", Tok("B")), None),
                    ),
                ),
            ),
        ),
        (
            "defn",
            "trait Show { fn show(*self) *u8; }",
            T("defn", "trait_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("Show")),
                None,
                T("trait_fn_decl").cs(
                    T("ident", Tok("show")),
                    T("param_list", "receiver", "mut", Tok(None)),
                    T("ptr_typ").cs(
                        T("mut", Tok(None)), T("basic_typ").cs(T("path", "ident", Tok("u8")), None)
                    ),
                ),
            ),
        ),
        (
            "defn",
            "pub trait Eq[T] { fn eq(*self, other: *T) bool; }",
            T("defn", "trait_defn").cs(
                T("access", Tok("pub")),
                T("ident", Tok("Eq")),
                T("comptime_params", "comptime_param", "ident", Tok("T")),
                T("trait_fn_decl").cs(
                    T("ident", Tok("eq")),
                    T("param_list").cs(
                        T("receiver", "mut", Tok(None)),
                        T("param").cs(
                            T("ident", Tok("other")),
                            T("ptr_typ").cs(
                                T("mut", Tok(None)),
                                T("basic_typ").cs(T("path", "ident", Tok("T")), None),
                            ),
                        ),
                    ),
                    T("basic_typ").cs(T("path", "ident", Tok("bool")), None),
                ),
            ),
        ),
        (
            "defn",
            "impl Foo {}",
            T("defn", "impl_defn").cs(
                None,
                T("basic_typ").cs(T("path", "ident", Tok("Foo")), None),
                None,
            ),
        ),
        (
            "defn",
            "impl[T] Pair[T, T] {}",
            T("defn", "impl_defn").cs(
                T("comptime_params", "comptime_param", "ident", Tok("T")),
                T("basic_typ").cs(
                    T("path", "ident", Tok("Pair")),
                    T("comptime_args").cs(
                        T("basic_typ").cs(T("path", "ident", Tok("T")), None),
                        T("basic_typ").cs(T("path", "ident", Tok("T")), None),
                    ),
                ),
                None,
            ),
        ),
        (
            "defn",
            "impl Show for i32 {}",
            T("defn", "impl_defn").cs(
                None,
                T("basic_typ").cs(T("path", "ident", Tok("Show")), None),
                T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
            ),
        ),
        (
            "defn",
            "impl *Foo {}",
            T("defn", "impl_defn").cs(
                None,
                T("ptr_typ").cs(
                    T("mut", Tok(None)), T("basic_typ").cs(T("path", "ident", Tok("Foo")), None)
                ),
                None,
            ),
        ),
        (
            "defn",
            "impl array[Foo, 3] {}",
            T("defn", "impl_defn").cs(
                None,
                T("basic_typ").cs(
                    T("path", "ident", Tok("array")),
                    T("comptime_args").cs(
                        T("basic_typ").cs(T("path", "ident", Tok("Foo")), None),
                        T("int_lit", Tok("3")),
                    ),
                ),
                None,
            ),
        ),
        (
            "defn",
            "fn id[T](x: T) T { return x; }",
            T("defn", "fn_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("id")),
                T("comptime_params", "comptime_param", "ident", Tok("T")),
                T("param_list").cs(
                    T("param").cs(
                        T("ident", Tok("x")), T("basic_typ").cs(T("path", "ident", Tok("T")), None)
                    ),
                ),
                T("basic_typ").cs(T("path", "ident", Tok("T")), None),
                T("block_expr", "stmt", "ret_stmt").cs(
                    T("var_expr").cs(T("path", "ident", Tok("x")), None)
                ),
            ),
        ),
        (
            "defn",
            "fn show_it[T: Show + Eq](x: T) {}",
            T("defn", "fn_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("show_it")),
                T("comptime_params").cs(
                    T("comptime_param").cs(
                        T("ident", Tok("T")),
                        T("bound_list").cs(
                            T("basic_typ").cs(T("path", "ident", Tok("Show")), None),
                            T("basic_typ").cs(T("path", "ident", Tok("Eq")), None),
                        ),
                    ),
                ),
                T("param_list").cs(
                    T("param").cs(
                        T("ident", Tok("x")), T("basic_typ").cs(T("path", "ident", Tok("T")), None)
                    ),
                ),
                None,
                T("block_expr"),
            ),
        ),
        (
            "typ",
            "Vec[i32]",
            T("basic_typ").cs(
                T("path", "ident", Tok("Vec")),
                T("comptime_args").cs(T("basic_typ").cs(T("path", "ident", Tok("i32")), None)),
            ),
        ),
        (
            "typ",
            "Pair[i32, bool]",
            T("basic_typ").cs(
                T("path", "ident", Tok("Pair")),
                T("comptime_args").cs(
                    T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    T("basic_typ").cs(T("path", "ident", Tok("bool")), None),
                ),
            ),
        ),
        (
            "typ",
            "Buf[i32, 4]",
            T("basic_typ").cs(
                T("path", "ident", Tok("Buf")),
                T("comptime_args").cs(
                    T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    T("int_lit", Tok("4")),
                ),
            ),
        ),
        (
            "typ",
            "Flag[true]",
            T("basic_typ").cs(
                T("path", "ident", Tok("Flag")),
                T("comptime_args").cs(T("bool_lit", Tok("true"))),
            ),
        ),
        (
            "typ",
            "Buf[i32, N]",
            T("basic_typ").cs(
                T("path", "ident", Tok("Buf")),
                T("comptime_args").cs(
                    T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    T("basic_typ").cs(T("path", "ident", Tok("N")), None),
                ),
            ),
        ),
        (
            "expr",
            "f[i32](x)",
            T("call_expr").cs(
                T("var_expr").cs(
                    T("path", "ident", Tok("f")),
                    T("comptime_args").cs(T("basic_typ").cs(T("path", "ident", Tok("i32")), None)),
                ),
                T("arg_list").cs(T("var_expr").cs(T("path", "ident", Tok("x")), None)),
            ),
        ),
        (
            # A variable reference's comptime_args accepts int_lit too, the
            # same as basic_typ's - this is no longer ambiguous with array
            # indexing, which requires the ".[" operator.
            "expr",
            "a[10]",
            T("var_expr").cs(
                T("path", "ident", Tok("a")),
                T("comptime_args").cs(T("int_lit", Tok("10"))),
            ),
        ),
        (
            "defn",
            "impl Foo { fn f() i32 { 1 } }",
            T("defn", "impl_defn").cs(
                None,
                T("basic_typ").cs(T("path", "ident", Tok("Foo")), None),
                None,
                T("fn_defn").cs(
                    T("access", Tok(None)),
                    T("ident", Tok("f")),
                    None,
                    T("param_list"),
                    T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    T("block_expr", "int_lit", Tok("1")),
                ),
            ),
        ),
        (
            "defn",
            "impl Foo { pub fn new() Foo { 1 } fn helper() i32 { 2 } }",
            T("defn", "impl_defn").cs(
                None,
                T("basic_typ").cs(T("path", "ident", Tok("Foo")), None),
                None,
                T("fn_defn").cs(
                    T("access", Tok("pub")),
                    T("ident", Tok("new")),
                    None,
                    T("param_list"),
                    T("basic_typ").cs(T("path", "ident", Tok("Foo")), None),
                    T("block_expr", "int_lit", Tok("1")),
                ),
                T("fn_defn").cs(
                    T("access", Tok(None)),
                    T("ident", Tok("helper")),
                    None,
                    T("param_list"),
                    T("basic_typ").cs(T("path", "ident", Tok("i32")), None),
                    T("block_expr", "int_lit", Tok("2")),
                ),
            ),
        ),
        (
            "defn",
            "import xyz;",
            T("defn", "import", "path", "ident", Tok("xyz")),
        ),
        (
            "defn",
            "import std::mem;",
            T("defn", "import").cs(
                T("path").cs(T("ident", Tok("std")), T("ident", Tok("mem"))),
            ),
        ),
        ("mod", "", T("mod")),
        (
            "mod",
            "import a; fn ab_cd(x: a, y: b) _rt { return 9; } fn x() y {}",
            T("mod").cs(
                T("defn", "import", "path", "ident", Tok("a")),
                T("defn", "fn_defn").cs(
                    T("access", Tok(None)),
                    T("ident", Tok("ab_cd")),
                    None,
                    T("param_list").cs(
                        T("param").cs(
                            T("ident", Tok("x")),
                            T("basic_typ").cs(T("path", "ident", Tok("a")), None),
                        ),
                        T("param").cs(
                            T("ident", Tok("y")),
                            T("basic_typ").cs(T("path", "ident", Tok("b")), None),
                        ),
                    ),
                    T("basic_typ").cs(T("path", "ident", Tok("_rt")), None),
                    T("block_expr", "stmt", "ret_stmt", "int_lit", Tok("9")),
                ),
                T("defn", "fn_defn").cs(
                    T("access", Tok(None)),
                    T("ident", Tok("x")),
                    None,
                    T("param_list"),
                    T("basic_typ").cs(T("path", "ident", Tok("y")), None),
                    T("block_expr"),
                ),
            ),
        ),
        (
            "mod",
            "struct Foo {} impl Foo { pub fn new() Foo { 1 } }",
            T("mod").cs(
                T("defn", "struct_defn").cs(
                    T("access", Tok(None)),
                    T("ident", Tok("Foo")),
                    None,
                    T("struct_field_defn_list"),
                ),
                T("defn", "impl_defn").cs(
                    None,
                    T("basic_typ").cs(T("path", "ident", Tok("Foo")), None),
                    None,
                    T("fn_defn").cs(
                        T("access", Tok("pub")),
                        T("ident", Tok("new")),
                        None,
                        T("param_list"),
                        T("basic_typ").cs(T("path", "ident", Tok("Foo")), None),
                        T("block_expr", "int_lit", Tok("1")),
                    ),
                ),
            ),
        ),
    ],
)
def test_parse(rule, src, expected):
    check_parse(rule, src, expected)


@pytest.mark.parametrize(
    "rule,src",
    [("int_lit", src) for src in NOT_INT_LITS]
    + [("str_lit", src) for src in NOT_STR_LITS]
    + [("ident", src) for src in NOT_IDENTS]
    + [
        ("var_expr", ""),
        ("var_expr", "a b"),
        ("var_expr", "0a"),
        ("var_expr", "a 0"),
        ("expr", ""),
        ("expr", "f(0"),
        ("expr", "f 0)"),
        ("expr", "f(,0)"),
        ("expr", "f(,)"),
        ("expr", "1+"),
        ("expr", "*a"),
        ("expr", "a < b < c"),
        ("expr", "a.1"),
        ("expr", ".a"),
        ("expr", "a."),
        ("block_expr", ""),
        ("block_expr", "{"),
        ("block_expr", "}"),
        ("block_expr", "{()}"),
        ("expr", "{ let x = if (true) { 1 } else { 2 } y }"),
        ("expr", "{ return if (true) { 1 } else { 2 } y }"),
        ("expr", "{ x = if (true) { 1 } else { 2 } y }"),
        ("expr", "{ Foo { a: 1 } y }"),
        ("stmt", ""),
        ("stmt", "let 0 = 1;"),
        ("stmt", "let x = 1"),
        ("stmt", "return"),
        ("stmt", "return ();"),
        ("stmt", "1 = 2;"),
        ("stmt", "f() = 2;"),
        ("stmt", "(1 + 2) = 2;"),
        ("stmt", "break"),
        ("stmt", "break 0;"),
        ("stmt", "continue"),
        ("stmt", "continue 0;"),
        ("expr", "1: while (true) {}"),
        ("expr", "lbl lbl: while (true) {}"),
        ("expr", "lbl:: while (true) {}"),
        ("expr", "match (x) { _ => {} _ => 1 }"),
        ("expr", "match x { _ => 1 }"),
        ("typ", ""),
        ("typ", "0"),
        ("typ", '"abc"'),
        ("typ", "a b"),
        ("typ", "a*"),
        ("typ", "Vec[]"),
        ("comptime_params", "[]"),
        ("comptime_params", "[T"),
        ("expr", "f[](x)"),
        ("struct_defn", "struct Foo[] {}"),
        ("impl_defn", "impl[] Foo {}"),
        ("trait_defn", "trait Foo { fn f() i32 {} }"),
        ("trait_defn", "trait Foo { fn f() i32 }"),
        ("typ", "[x: -10]"),
        ("typ", "[x: N]"),
        ("param", ""),
        ("param", "x"),
        ("param", "x:"),
        ("param", ":x"),
        ("param", "x:0"),
        ("param", "0:x"),
        ("param", "self"),
        ("param_list", "self"),
        ("fn_defn", ""),
        ("fn_defn", "fn f(,) a {}"),
        ("fn_defn", "fn () a {}"),
        ("fn_defn", "f() a {}"),
        ("fn_defn", "f();"),
        ("impl_defn", ""),
        ("impl_defn", "impl {}"),
        ("impl_defn", "impl Foo"),
        ("impl_defn", "impl Foo {"),
        ("impl_defn", "Foo {}"),
        ("impl_defn", "impl Foo { extern fn f() i32; }"),
        ("impl_defn", "impl Foo { struct Bar {} }"),
        ("impl_defn", "impl Foo { let x = 1; }"),
        ("impl_defn", "impl Foo { import bar; }"),
        ("impl_defn", "impl Foo { impl Bar {} }"),
        ("impl_defn", "pub impl Foo {}"),
        ("mod", "1"),
        ("mod", "{}"),
        ("mod", "f(0);"),
        ("expr", "// just a comment"),
        ("expr", "/* not a supported comment style */ 1"),
    ],
)
def test_parse_fails(rule, src):
    check_parse_fails(rule, src)
