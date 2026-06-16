from dataclasses import dataclass
from difflib import ndiff
from typing import Optional

from lark import Tree, UnexpectedCharacters, UnexpectedToken
import pytest

from l0.main import build_parser


@dataclass
class Tok:
    value: Optional[str]


class T(Tree):
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

    def cs(self, *children):
        assert self.leaf is not None
        self.leaf.children.extend(children)
        self.leaf = None
        return self


def check_parse(rule, src, expected):
    p = build_parser(rule)
    tree = p.parse(src)

    if tree != expected:
        got_str = tree.pretty()
        expected_str = expected.pretty()

        print("GOT:\n")
        print(got_str)

        print("EXPECTED:\n")
        print(expected_str)

        print("DIFF:\n")
        diff = ndiff(
            got_str.splitlines(keepends=True),
            expected_str.splitlines(keepends=True),
        )
        print("".join(diff))

        assert tree == expected


def check_parse_fails(rule, src):
    p = build_parser(rule)
    with pytest.raises((UnexpectedToken, UnexpectedCharacters)):
        tree = p.parse(src)
        print(tree)


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
IDENTS = ["_", "a", "A", "_0", "abc_0de", " zZ", "v10_ "]
NOT_IDENTS = ["", '"abc"', "0", "9", "1a", "a b", "0_", "("]


@pytest.mark.parametrize(
    "rule,src,expected",
    [("expr", src, T("int_lit", Tok(src.strip()))) for src in INT_LITS]
    + [("expr", src, T("str_lit", Tok(src.strip()))) for src in STR_LITS]
    + [("expr", src, T("var_expr", "ident", Tok(src.strip()))) for src in IDENTS]
    + [
        ("expr", "(1)", T("int_lit", Tok("1"))),
        ("expr", '("x")', T("str_lit", Tok('"x"'))),
        ("expr", "(x)", T("var_expr", "ident", Tok("x"))),
        (
            "expr",
            'abc(123, "xyz", abc_def, f())',
            T("call_expr").cs(
                T("var_expr", "ident", Tok("abc")),
                T("arg_list").cs(
                    T("int_lit", Tok("123")),
                    T("str_lit", Tok('"xyz"')),
                    T("var_expr", "ident", Tok("abc_def")),
                    T("call_expr").cs(T("var_expr", "ident", Tok("f")), T("arg_list")),
                ),
            ),
        ),
        (
            "expr",
            "abc()",
            T("call_expr").cs(T("var_expr", "ident", Tok("abc")), T("arg_list")),
        ),
        (
            "expr",
            "_(0,)",
            T("call_expr").cs(
                T("var_expr", "ident", Tok("_")),
                T("arg_list", "int_lit", Tok("0")),
            ),
        ),
        (
            "expr",
            "[1, 2, a, b]",
            T("array_expr", "arg_list").cs(
                T("int_lit", Tok("1")),
                T("int_lit", Tok("2")),
                T("var_expr", "ident", Tok("a")),
                T("var_expr", "ident", Tok("b")),
            ),
        ),
        ("expr", "[]", T("array_expr", "arg_list")),
        (
            "expr",
            "a[0]",
            T("array_access_expr").cs(
                T("var_expr", "ident", Tok("a")),
                T("int_lit", Tok("0")),
            ),
        ),
        (
            "expr",
            "f()[x]",
            T("array_access_expr").cs(
                T("call_expr").cs(
                    T("var_expr", "ident", Tok("f")),
                    T("arg_list"),
                ),
                T("var_expr", "ident", Tok("x")),
            ),
        ),
        (
            "expr",
            "s { a: 10, b: x, c: f(), }",
            T("struct_expr").cs(
                T("basic_typ", "ident", Tok("s")),
                T("struct_field_expr_list").cs(
                    T("struct_field_expr").cs(
                        T("ident", Tok("a")),
                        T("int_lit", Tok("10")),
                    ),
                    T("struct_field_expr").cs(
                        T("ident", Tok("b")),
                        T("var_expr", "ident", Tok("x")),
                    ),
                    T("struct_field_expr").cs(
                        T("ident", Tok("c")),
                        T("call_expr").cs(
                            T("var_expr", "ident", Tok("f")),
                            T("arg_list"),
                        ),
                    ),
                ),
            ),
        ),
        (
            "expr",
            "_empty_struct { }",
            T("struct_expr").cs(
                T("basic_typ", "ident", Tok("_empty_struct")),
                T("struct_field_expr_list"),
            ),
        ),
        (
            "expr",
            "a.b",
            T("struct_access_expr").cs(
                T("var_expr", "ident", Tok("a")),
                T("ident", Tok("b")),
            ),
        ),
        (
            "expr",
            "a.b.c",
            T("struct_access_expr").cs(
                T("struct_access_expr").cs(
                    T("var_expr", "ident", Tok("a")),
                    T("ident", Tok("b")),
                ),
                T("ident", Tok("c")),
            ),
        ),
        (
            "expr",
            "arr[10].xyz",
            T("struct_access_expr").cs(
                T("array_access_expr").cs(
                    T("var_expr", "ident", Tok("arr")),
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
                T("var_expr", "ident", Tok("a")),
                T("add_op", Tok("+")),
                T("call_expr").cs(T("var_expr", "ident", Tok("f")), T("arg_list")),
            ),
        ),
        (
            "expr",
            "a < b",
            T("cmp_expr").cs(
                T("var_expr", "ident", Tok("a")),
                T("cmp_op", Tok("<")),
                T("var_expr", "ident", Tok("b")),
            ),
        ),
        (
            "expr",
            "a <= b + 1",
            T("cmp_expr").cs(
                T("var_expr", "ident", Tok("a")),
                T("cmp_op", Tok("<=")),
                T("arith_expr").cs(
                    T("var_expr", "ident", Tok("b")),
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
                    T("var_expr", "ident", Tok("a")),
                    T("add_op", Tok("+")),
                    T("int_lit", Tok("1")),
                ),
                T("cmp_op", Tok("==")),
                T("var_expr", "ident", Tok("b")),
            ),
        ),
        (
            "expr",
            "a * 1 - 2 >= b",
            T("cmp_expr").cs(
                T("arith_expr").cs(
                    T("term").cs(
                        T("var_expr", "ident", Tok("a")),
                        T("mul_op", Tok("*")),
                        T("int_lit", Tok("1")),
                    ),
                    T("add_op", Tok("-")),
                    T("int_lit", Tok("2")),
                ),
                T("cmp_op", Tok(">=")),
                T("var_expr", "ident", Tok("b")),
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
                T("var_expr", "ident", Tok("a")),
            ),
        ),
        (
            "expr",
            "&a * 1",
            T("term").cs(
                T("factor").cs(
                    T("unary_op", Tok("&")),
                    T("var_expr", "ident", Tok("a")),
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
                    T("var_expr", "ident", Tok("a")),
                ),
            ),
        ),
        (
            "expr",
            "&a.b",
            T("factor").cs(
                T("unary_op", Tok("&")),
                T("struct_access_expr").cs(
                    T("var_expr", "ident", Tok("a")),
                    T("ident", Tok("b")),
                ),
            ),
        ),
        (
            "expr",
            "&a.*",
            T("factor").cs(
                T("unary_op", Tok("&")),
                T("deref_expr", "var_expr", "ident", Tok("a")),
            ),
        ),
        (
            "expr",
            "a.*.b",
            T("struct_access_expr").cs(
                T("deref_expr", "var_expr", "ident", Tok("a")),
                T("ident", Tok("b")),
            ),
        ),
        (
            "stmt",
            "let x = 1;",
            T("stmt", "let_stmt").cs(
                T("mut", Tok(None)), T("ident", Tok("x")), T("int_lit", Tok("1"))
            ),
        ),
        (
            "stmt",
            "let mut x = 10;",
            T("stmt", "let_stmt").cs(
                T("mut", Tok("mut")), T("ident", Tok("x")), T("int_lit", Tok("10"))
            ),
        ),
        ("stmt", "return;", T("stmt", "ret_stmt", Tok(None))),
        (
            "stmt",
            'return "abc";',
            T("stmt", "ret_stmt", "str_lit", Tok('"abc"')),
        ),
        (
            "stmt",
            "x = y;",
            T("stmt", "assignment_stmt").cs(
                T("var_expr", "ident", Tok("x")), T("var_expr", "ident", Tok("y"))
            ),
        ),
        (
            "stmt",
            "x[10] = y;",
            T("stmt", "assignment_stmt").cs(
                T("array_access_expr").cs(
                    T("var_expr", "ident", Tok("x")),
                    T("int_lit", Tok("10")),
                ),
                T("var_expr", "ident", Tok("y")),
            ),
        ),
        (
            "stmt",
            "f()[1] = 2;",
            T("stmt", "assignment_stmt").cs(
                T("array_access_expr").cs(
                    T("call_expr").cs(T("var_expr", "ident", Tok("f")), T("arg_list")),
                    T("int_lit", Tok("1")),
                ),
                T("int_lit", Tok("2")),
            ),
        ),
        (
            "stmt",
            "x.y = z;",
            T("stmt", "assignment_stmt").cs(
                T("struct_access_expr").cs(
                    T("var_expr", "ident", Tok("x")),
                    T("ident", Tok("y")),
                ),
                T("var_expr", "ident", Tok("z")),
            ),
        ),
        (
            "stmt",
            "x.* = y;",
            T("stmt", "assignment_stmt").cs(
                T("deref_expr", "var_expr", "ident", Tok("x")),
                T("var_expr", "ident", Tok("y")),
            ),
        ),
        (
            "stmt",
            "abc();",
            T("stmt", "expr_stmt", "call_expr").cs(
                T("var_expr", "ident", Tok("abc")), T("arg_list")
            ),
        ),
        (
            "expr",
            "{ f(0); return 1; }",
            T("block_expr").cs(
                T("stmt", "expr_stmt", "call_expr").cs(
                    T("var_expr", "ident", Tok("f")),
                    T("arg_list", "int_lit", Tok("0")),
                ),
                T("stmt", "ret_stmt", "int_lit", Tok("1")),
            ),
        ),
        ("expr", "{}", T("block_expr")),
        ("typ", "i32", T("basic_typ", "ident", Tok("i32"))),
        ("typ", "x", T("basic_typ", "ident", Tok("x"))),
        ("typ", "_", T("basic_typ", "ident", Tok("_"))),
        ("typ", "*u8", T("ptr_typ", "basic_typ", "ident", Tok("u8"))),
        (
            "typ",
            "[a; 10]",
            T("array_typ").cs(
                T("basic_typ", "ident", Tok("a")),
                T("array_length", Tok("10")),
            ),
        ),
        (
            "typ",
            "[**u8; 0]",
            T("array_typ").cs(
                T("ptr_typ", "ptr_typ", "basic_typ", "ident", Tok("u8")),
                T("array_length", Tok("0")),
            ),
        ),
        (
            "typ",
            "[[u8; 10]; 5]",
            T("array_typ").cs(
                T("array_typ").cs(
                    T("basic_typ", "ident", Tok("u8")),
                    T("array_length", Tok("10")),
                ),
                T("array_length", Tok("5")),
            ),
        ),
        (
            "typ",
            "*[u8; 1]",
            T("ptr_typ", "array_typ").cs(
                T("basic_typ", "ident", Tok("u8")),
                T("array_length", Tok("1")),
            ),
        ),
        (
            "param",
            "x: i32",
            T("param").cs(T("ident", Tok("x")), T("basic_typ", "ident", Tok("i32"))),
        ),
        (
            "param",
            "x :i32",
            T("param").cs(T("ident", Tok("x")), T("basic_typ", "ident", Tok("i32"))),
        ),
        (
            "param",
            "x : i32",
            T("param").cs(T("ident", Tok("x")), T("basic_typ", "ident", Tok("i32"))),
        ),
        (
            "defn",
            "fn f() {}",
            T("defn", "fn_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("f")),
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
                T("param_list"),
                T("basic_typ", "ident", Tok("i32")),
                T("block_expr"),
            ),
        ),
        (
            "defn",
            "pub fn ab_cd(x: a, y: b,) _rt { return 9; }",
            T("defn", "fn_defn").cs(
                T("access", Tok("pub")),
                T("ident", Tok("ab_cd")),
                T("param_list").cs(
                    T("param").cs(
                        T("ident", Tok("x")), T("basic_typ", "ident", Tok("a"))
                    ),
                    T("param").cs(
                        T("ident", Tok("y")), T("basic_typ", "ident", Tok("b"))
                    ),
                ),
                T("basic_typ", "ident", Tok("_rt")),
                T("block_expr", "stmt", "ret_stmt", "int_lit", Tok("9")),
            ),
        ),
        (
            "defn",
            "struct AStruct {a: i32, pub b: *u8, c: AnotherStruct}",
            T("defn", "struct_defn").cs(
                T("access", Tok(None)),
                T("ident", Tok("AStruct")),
                T("struct_field_defn_list").cs(
                    T("struct_field_defn").cs(
                        T("access", Tok(None)),
                        T("ident", Tok("a")),
                        T("basic_typ", "ident", Tok("i32")),
                    ),
                    T("struct_field_defn").cs(
                        T("access", Tok("pub")),
                        T("ident", Tok("b")),
                        T("ptr_typ", "basic_typ", "ident", Tok("u8")),
                    ),
                    T("struct_field_defn").cs(
                        T("access", Tok(None)),
                        T("ident", Tok("c")),
                        T("basic_typ", "ident", Tok("AnotherStruct")),
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
                T("struct_field_defn_list"),
            ),
        ),
        ("mod", "", T("mod")),
        ("mod", "", T("mod")),
        (
            "mod",
            "fn ab_cd(x: a, y: b) _rt { return 9; } fn x() y {}",
            T("mod").cs(
                T("defn", "fn_defn").cs(
                    T("access", Tok(None)),
                    T("ident", Tok("ab_cd")),
                    T("param_list").cs(
                        T("param").cs(
                            T("ident", Tok("x")), T("basic_typ", "ident", Tok("a"))
                        ),
                        T("param").cs(
                            T("ident", Tok("y")), T("basic_typ", "ident", Tok("b"))
                        ),
                    ),
                    T("basic_typ", "ident", Tok("_rt")),
                    T("block_expr", "stmt", "ret_stmt", "int_lit", Tok("9")),
                ),
                T("defn", "fn_defn").cs(
                    T("access", Tok(None)),
                    T("ident", Tok("x")),
                    T("param_list"),
                    T("basic_typ", "ident", Tok("y")),
                    T("block_expr"),
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
        ("expr", "a.[10]"),
        ("expr", ".a"),
        ("expr", "a."),
        ("block_expr", ""),
        ("block_expr", "{"),
        ("block_expr", "}"),
        ("block_expr", "{()}"),
        ("stmt", ""),
        ("stmt", "let 0 = 1;"),
        ("stmt", "let x = 1"),
        ("stmt", "return"),
        ("stmt", "return ();"),
        ("stmt", "1 = 2;"),
        ("stmt", "f() = 2;"),
        ("stmt", "(1 + 2) = 2;"),
        ("typ", ""),
        ("typ", "0"),
        ("typ", '"abc"'),
        ("typ", "a b"),
        ("typ", "a*"),
        ("typ", "[x: -10]"),
        ("typ", "[x: N]"),
        ("param", ""),
        ("param", "x"),
        ("param", "x:"),
        ("param", ":x"),
        ("param", "x:0"),
        ("param", "0:x"),
        ("fn_defn", ""),
        ("fn_defn", "fn f(,) a {}"),
        ("fn_defn", "fn () a {}"),
        ("fn_defn", "f() a {}"),
        ("fn_defn", "f();"),
        ("mod", "1"),
        ("mod", "{}"),
        ("mod", "f(0);"),
    ],
)
def test_parse_fails(rule, src):
    check_parse_fails(rule, src)
