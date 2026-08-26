# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""The parsed AST: one node class per grammar rule, built from Lark parse trees."""

import abc
import ast as python_ast
import re
from collections.abc import Callable
from typing import Any, Final, Optional, override

import lark
import lark.tree

from leech import asserts, opt_util, signage, src, target


def _as_token(branch: lark.tree.Branch[lark.Token]) -> lark.Token:
    return asserts.checked_cast(branch, lark.Token)


def _as_tree(branch: lark.tree.Branch[lark.Token]) -> lark.tree.Tree[lark.Token]:
    return asserts.checked_cast(branch, lark.tree.Tree)


class Ast(abc.ABC):
    """Base class for every AST node."""

    span: Final[src.SrcSpan]

    def __init__(self, span: src.SrcSpan) -> None:
        self.span = span

    @staticmethod
    def _pretty_child(child_name: str, child_val: Any, indent: str, level: int) -> str:
        match child_val:
            case Ast():
                return f"{indent * level}{child_name}: {child_val.pretty(indent, level)}"
            case list() | tuple():
                next_level = level + 1
                elts = "".join(
                    f"\n{indent * next_level}{x.pretty(indent, level + 1)}" for x in child_val
                )
                return f"{indent * level}{child_name}:{elts}"
            case _:
                return f"{indent * level}{child_val!r}"

    def pretty(self, indent: str = "  ", level: int = 0) -> str:
        """Render this node and its children as an indented debugging tree."""
        cls_name = type(self).__name__
        child_attrs = ((k, v) for k, v in vars(self).items() if k != "span")
        child_strs = "\n".join(Ast._pretty_child(k, v, indent, level + 1) for k, v in child_attrs)
        return f"{cls_name}\n{child_strs}"

    @abc.abstractmethod
    def diag_str(self) -> str:
        """Return a short description of this node for diagnostics."""


class Ident(Ast):
    """A single identifier."""

    name: Final[str]

    def __init__(self, span: src.SrcSpan, name: str) -> None:
        super().__init__(span)
        self.name = name

    @classmethod
    def from_tree(cls, file: src.SrcFile, tree: lark.tree.ParseTree) -> Ident:
        """Build an identifier from a parse tree."""
        asserts.assert_eq(tree.data, "ident")
        span = src.SrcSpan.from_lark_meta(file, tree.meta)
        return cls(span, _as_token(tree.children[0]))

    @staticmethod
    def _synthetic(name: str, span: src.SrcSpan) -> Ident:
        """Construct an identifier not backed by a parse tree."""
        return Ident(span, name)

    @override
    def diag_str(self) -> str:
        return f"identifier {self.name}"


class Path(Ast):
    """A (possibly ``::``-qualified) sequence of identifiers, e.g. ``some_mod::Foo``."""

    idents: Final[tuple[Ident, ...]]

    @override
    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "path")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        self.idents = tuple(Ident.from_tree(file, _as_tree(ident)) for ident in tree.children)

    @override
    def diag_str(self) -> str:
        return f"path {self.str()}"

    def str(self) -> str:
        """This path rendered back as ``::``-separated source text."""
        return "::".join(ident.name for ident in self.idents)


class Expr(Ast):
    """Base class for every expression node."""

    @staticmethod
    def _child_ctors() -> dict[str, Callable[[src.SrcFile, lark.tree.Tree], Expr]]:
        return {
            "block_expr": BlockExpr,
            "if_expr": IfExpr,
            "while_expr": WhileExpr,
            "call_expr": CallExpr,
            "int_lit": IntLit,
            "str_lit": StrLit,
            "bool_lit": BoolLit,
            "var_expr": VarExpr,
            "array_access_expr": ArrayAccessExpr,
            "array_expr": ArrayExpr,
            "struct_expr": StructExpr,
            "field_access_expr": FieldAccessExpr,
            "deref_expr": DerefExpr,
            "or_expr": BinOpExpr,
            "and_expr": BinOpExpr,
            "not_expr": UnaryOpExpr,
            "cmp_expr": BinOpExpr,
            "arith_expr": BinOpExpr,
            "term": BinOpExpr,
            "factor": UnaryOpExpr,
        }

    @staticmethod
    def _is_expr_tree(tree: lark.tree.ParseTree) -> bool:
        """Return whether ``tree``'s grammar rule produces an expression."""
        return tree.data in Expr._child_ctors()

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Expr:
        """Build the expression selected by a parse tree's grammar rule."""
        return Expr._child_ctors()[tree.data](file, tree)


class PlaceExpr(Expr):
    """Base class for expressions that can appear on the left of ``=``."""

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> PlaceExpr:
        """Build a place expression, asserting that the parsed expression is assignable."""
        return asserts.checked_cast(Expr.from_tree(file, tree), PlaceExpr)


class BlockExpr(Expr):
    """A ``{ stmt; ...; tail_expr }`` block expression."""

    stmts: Final[tuple[Stmt, ...]]
    expr: Final[Optional[Expr]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "block_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))

        children = list(map(_as_tree, tree.children))

        if len(children) > 0 and Expr._is_expr_tree(children[-1]):
            stmts = children[0:-1]
            expr = children[-1]
        else:
            stmts = children
            expr = None

        self.stmts = tuple(BlockExpr._stmt_from_tree(file, stmt) for stmt in stmts)

        if expr:
            self.expr = Expr.from_tree(file, expr)
        else:
            self.expr = None

    @staticmethod
    def _stmt_from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Stmt:
        """Build a block-body statement, which may be a ``;``-terminated ``stmt``
        or a bare block-like expression used without one."""
        if tree.data == "stmt":
            return Stmt.from_tree(file, tree)
        return ExprStmt._synthetic(Expr.from_tree(file, tree))

    @override
    def diag_str(self) -> str:
        return "block expression"


class IfExpr(Expr):
    """An ``if (condition) { ... } else { ... }`` expression."""

    condition: Final[Expr]
    then: Final[BlockExpr]
    els: Final[Optional[BlockExpr]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "if_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))

        condition, then, *rest = list(map(_as_tree, tree.children))
        self.condition = Expr.from_tree(file, condition)
        self.then = BlockExpr(file, then)

        if rest:
            (els,) = rest
            self.els = BlockExpr(file, els)
        else:
            self.els = None

    @override
    def diag_str(self) -> str:
        return "if expression"


class WhileExpr(Expr):
    """A ``[label:] while (condition) { ... }`` loop expression."""

    label: Final[Optional[Ident]]
    condition: Final[Expr]
    block: Final[BlockExpr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "while_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        label, condition, block = tree.children
        self.label = opt_util.opt_map(label, lambda x: Ident.from_tree(file, _as_tree(x)))
        self.condition = Expr.from_tree(file, _as_tree(condition))
        self.block = BlockExpr(file, _as_tree(block))

    @override
    def diag_str(self) -> str:
        return "while expression"


class StrLit(Expr):
    """A string literal."""

    value: Final[str]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "str_lit")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (tok,) = tree.children
        self.value = python_ast.literal_eval(_as_token(tok))

    @override
    def diag_str(self) -> str:
        return "string literal"


class IntLit(Expr):
    """An integer literal with optional explicit width and signedness."""

    token: Final[lark.Token]
    value: Final[int]
    explicit_width: Final[Optional[int]]
    explicit_signage: Final[Optional[signage.Signage]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "int_lit")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (token,) = tree.children
        self.token = _as_token(token)
        m = re.fullmatch("([0-9]+)(?:([iu])((?:[0-9]+)|size))?", self.token)
        assert m is not None

        if m[2] is not None:
            self.explicit_signage = signage.SIGNED if m[2] == "i" else signage.UNSIGNED
            self.explicit_width = target.ADDR_SIZE if m[3] == "size" else int(m[3])
        else:
            self.explicit_signage = None
            self.explicit_width = None

        self.value = int(m[1])

    @override
    def diag_str(self) -> str:
        return f'int literal "{self.token}"'


class ArrayLength(Ast):
    """Base class for a parsed array type's length: a literal, e.g. the
    ``4`` in ``[i32; 4]``, or a reference to an in-scope value parameter,
    e.g. the ``N`` in ``[i32; N]``.
    """

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> ArrayLength:
        """Build the array length selected by a parse tree's grammar rule."""
        asserts.assert_eq(tree.data, "array_length")
        (child,) = tree.children
        if isinstance(child, lark.Token):
            return LiteralArrayLength(file, tree)
        return PathArrayLength(file, tree)


class LiteralArrayLength(ArrayLength):
    """An array length given as an integer literal, e.g. the ``4`` in ``[i32; 4]``."""

    value: Final[int]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (token,) = tree.children
        self.value = int(_as_token(token))

    @override
    def diag_str(self) -> str:
        return f'array length "{self.value}"'


class PathArrayLength(ArrayLength):
    """An array length given as a value parameter reference, e.g. the ``N`` in ``[i32; N]``."""

    path: Final[Path]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (child,) = tree.children
        self.path = Path(file, _as_tree(child))

    @override
    def diag_str(self) -> str:
        return f'array length "{self.path.str()}"'


class BoolLit(Expr):
    """A boolean literal, ``true`` or ``false``."""

    token: Final[lark.Token]
    value: Final[bool]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "bool_lit")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (token,) = tree.children
        self.token = _as_token(token)
        asserts.assert_in(token, {"true", "false"})
        self.value = token == "true"

    @override
    def diag_str(self) -> str:
        return f'bool literal "{self.token}"'


class VarExpr(PlaceExpr):
    """A (possibly qualified) variable or function reference, e.g. ``some_mod::x``.

    ``comptime_args`` holds an explicit comptime application, e.g. the
    ``[i32]`` in ``f[i32](x)`` or the ``[4]`` in ``f[4](x)``; empty when
    the callee's comptime parameters (if any) are left to be inferred.
    """

    path: Final[Path]
    comptime_args: Final[tuple[ComptimeArg, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        path, comptime_args = tree.children
        self.path = Path(file, _as_tree(path))
        if comptime_args is not None:
            self.comptime_args = tuple(
                _comptime_arg_from_tree(file, _as_tree(c)) for c in _as_tree(comptime_args).children
            )
        else:
            self.comptime_args = ()

    @override
    def diag_str(self) -> str:
        return f'variable "{self.path.str()}"'


class ArrayAccessExpr(PlaceExpr):
    """An array indexing expression, ``array[index]``."""

    array: Final[Expr]
    index: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "array_access_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        array, index = map(_as_tree, tree.children)
        self.array = Expr.from_tree(file, array)
        self.index = Expr.from_tree(file, index)

    @override
    def diag_str(self) -> str:
        return "array access expression"


class ArrayExpr(Expr):
    """An array literal, e.g. ``[1, 2, 3]``."""

    elements: Final[tuple[Expr, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "array_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (arg_list,) = map(_as_tree, tree.children)
        asserts.assert_eq(arg_list.data, "arg_list")
        self.elements = tuple(Expr.from_tree(file, _as_tree(child)) for child in arg_list.children)

    @override
    def diag_str(self) -> str:
        return "array expression"


class StructExpr(Expr):
    """A struct literal, e.g. ``Point { x: 1, y: 2 }``."""

    typ: Final[BasicTyp]
    fields: Final[tuple[StructFieldExpr, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "struct_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        typ, field_list = map(_as_tree, tree.children)
        self.typ = BasicTyp(file, typ)

        asserts.assert_eq(field_list.data, "struct_field_expr_list")
        self.fields = tuple(StructFieldExpr(file, _as_tree(child)) for child in field_list.children)

    @override
    def diag_str(self) -> str:
        return f'struct "{self.typ.path.str()}" expression'


class StructFieldExpr(Expr):
    """A single ``field: value`` entry in a struct literal."""

    ident: Final[Ident]
    value: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "struct_field_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        ident, value = map(_as_tree, tree.children)
        self.ident = Ident.from_tree(file, ident)
        self.value = Expr.from_tree(file, value)

    @override
    def diag_str(self) -> str:
        return f'struct field "{self.ident.name}" expression'


class FieldAccessExpr(PlaceExpr):
    """A field access expression, ``value.field``.

    Despite the name, ``value`` isn't necessarily a struct: this node is
    also used for method-call syntax (``value.method(...)``), and a
    method can be defined on any type via an ``impl`` block, not just a
    struct.
    """

    value: Final[Expr]
    field: Final[Ident]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "field_access_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        value, field = map(_as_tree, tree.children)
        self.value = Expr.from_tree(file, value)
        self.field = Ident.from_tree(file, field)

    @override
    def diag_str(self) -> str:
        return "field access expression"


class DerefExpr(PlaceExpr):
    """A pointer dereference expression, ``ptr.*``."""

    ptr: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "deref_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (ptr,) = map(_as_tree, tree.children)
        self.ptr = Expr.from_tree(file, ptr)

    @override
    def diag_str(self) -> str:
        return "dereference expression"


class CallExpr(Expr):
    """A function call expression, ``callee(args...)``."""

    callee: Final[Expr]
    args: Final[tuple[Expr, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "call_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        callee, arg_list = map(_as_tree, tree.children)
        self.callee = Expr.from_tree(file, callee)

        asserts.assert_eq(arg_list.data, "arg_list")
        self.args = tuple(Expr.from_tree(file, _as_tree(child)) for child in arg_list.children)

    @override
    def diag_str(self) -> str:
        return "call expression"


class Op(Ast):
    """A binary or unary operator token, e.g. ``+`` or ``&``."""

    name: Final[str]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        asserts.assert_eq(len(tree.children), 1)
        self.name = _as_token(tree.children[0])

    @override
    def diag_str(self) -> str:
        return f"{self.name} operation"


class BinOpExpr(Expr):
    """A binary operator expression, ``lhs op rhs``."""

    lhs: Final[Expr]
    op: Final[Op]
    rhs: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        lhs, op, rhs = map(_as_tree, tree.children)
        self.lhs = Expr.from_tree(file, lhs)
        self.op = Op(file, op)
        self.rhs = Expr.from_tree(file, rhs)

    @override
    def diag_str(self) -> str:
        return f"binary {self.op.name} operation expression"


class UnaryOpExpr(Expr):
    """A unary operator expression, ``op operand`` (``&operand`` or ``-operand``)."""

    op: Final[Op]
    operand: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        op, operand = map(_as_tree, tree.children)
        self.op = Op(file, op)
        self.operand = Expr.from_tree(file, operand)

    @override
    def diag_str(self) -> str:
        return f"unary {self.op.name} operation expression"


class Stmt(Ast):
    """Base class for every statement node."""

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Stmt:
        """Build the statement selected by a parse tree's grammar rule."""
        asserts.assert_eq(tree.data, "stmt")
        (child,) = map(_as_tree, tree.children)
        if child.data == "expr_stmt":
            return ExprStmt._from_expr_stmt_tree(file, child)
        child_classes = {
            "ret_stmt": RetStmt,
            "let_stmt": LetStmt,
            "assignment_stmt": AssignmentStmt,
            "break_stmt": BreakStmt,
            "continue_stmt": ContinueStmt,
        }
        return child_classes[child.data](file, child)


class ExprStmt(Stmt):
    """An expression evaluated for its side effects, with or without a trailing ``;``."""

    expr: Final[Expr]

    def __init__(self, span: src.SrcSpan, expr: Expr) -> None:
        super().__init__(span)
        self.expr = expr

    @staticmethod
    def _from_expr_stmt_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> ExprStmt:
        asserts.assert_eq(tree.data, "expr_stmt")
        span = src.SrcSpan.from_lark_meta(file, tree.meta)
        (child,) = tree.children
        return ExprStmt(span, Expr.from_tree(file, _as_tree(child)))

    @staticmethod
    def _synthetic(expr: Expr) -> ExprStmt:
        """Construct a statement for a block-like expression used without a trailing ``;``."""
        return ExprStmt(expr.span, expr)

    @override
    def diag_str(self) -> str:
        return "expression statement"


class RetStmt(Stmt):
    """A ``return`` statement, with or without a value."""

    expr: Final[Optional[Expr]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "ret_stmt")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (expr,) = tree.children
        self.expr = opt_util.opt_map(expr, lambda x: Expr.from_tree(file, _as_tree(x)))

    @override
    def diag_str(self) -> str:
        return "return statement"


class BreakStmt(Stmt):
    """A ``break`` statement, with or without a target loop label."""

    label: Final[Optional[Ident]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "break_stmt")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (label,) = tree.children
        self.label = opt_util.opt_map(label, lambda x: Ident.from_tree(file, _as_tree(x)))

    @override
    def diag_str(self) -> str:
        return "break statement"


class ContinueStmt(Stmt):
    """A ``continue`` statement, with or without a target loop label."""

    label: Final[Optional[Ident]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "continue_stmt")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (label,) = tree.children
        self.label = opt_util.opt_map(label, lambda x: Ident.from_tree(file, _as_tree(x)))

    @override
    def diag_str(self) -> str:
        return "continue statement"


class LetStmt(Stmt):
    """A local ``let`` binding statement, with an optional declared type."""

    mut: Final[Optional[Mutability]]
    ident: Final[Ident]
    typ: Final[Optional[Typ]]
    expr: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "let_stmt")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        mut, ident, typ, expr = tree.children
        self.mut = Mutability.from_tree(file, _as_tree(mut))
        self.ident = Ident.from_tree(file, _as_tree(ident))
        self.typ = opt_util.opt_map(typ, lambda x: Typ.from_tree(file, _as_tree(x)))
        self.expr = Expr.from_tree(file, _as_tree(expr))

    @override
    def diag_str(self) -> str:
        return "let statement"


class AssignmentStmt(Stmt):
    """An assignment statement, ``place = expr``."""

    place: Final[PlaceExpr]
    expr: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "assignment_stmt")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        place, expr = map(_as_tree, tree.children)
        self.place = PlaceExpr.from_tree(file, place)
        self.expr = Expr.from_tree(file, expr)

    @override
    def diag_str(self) -> str:
        return "assignment statement"


class Typ(Ast):
    """Base class for parsed, unresolved type expressions."""

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Typ:
        """Build the type expression selected by a parse tree's grammar rule."""
        child_classes = {
            "basic_typ": BasicTyp,
            "ptr_typ": PtrTyp,
            "array_typ": ArrayTyp,
        }
        return child_classes[tree.data](file, tree)


type ComptimeArg = Typ | IntLit | BoolLit
"""A parsed comptime argument: a type, or a literal value."""


def _comptime_arg_from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> ComptimeArg:
    """Build a comptime argument - a type or a value literal - from its parse tree."""
    if tree.data == "int_lit":
        return IntLit(file, tree)
    if tree.data == "bool_lit":
        return BoolLit(file, tree)
    return Typ.from_tree(file, tree)


class BasicTyp(Typ):
    """A named type, e.g. ``i32`` or ``some_mod::Foo``, optionally applied to
    generic arguments, e.g. ``Vec[i32]``. ``comptime_args`` is empty for a
    non-generic type.
    """

    path: Final[Path]
    comptime_args: Final[tuple[ComptimeArg, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "basic_typ")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        path, comptime_args = tree.children
        self.path = Path(file, _as_tree(path))
        if comptime_args is not None:
            self.comptime_args = tuple(
                _comptime_arg_from_tree(file, _as_tree(c)) for c in _as_tree(comptime_args).children
            )
        else:
            self.comptime_args = ()

    @override
    def diag_str(self) -> str:
        return f'type specifier "{self.path.str()}"'


class PtrTyp(Typ):
    """A pointer type expression, e.g. ``*i32`` or ``*mut i32``."""

    mut: Final[Optional[Mutability]]
    pointee_typ: Final[Typ]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "ptr_typ")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        mut, typ = tree.children
        self.mut = Mutability.from_tree(file, _as_tree(mut))
        self.pointee_typ = Typ.from_tree(file, _as_tree(typ))

    @override
    def diag_str(self) -> str:
        return "pointer type specifier"


class ArrayTyp(Typ):
    """A fixed-length array type expression, e.g. ``[i32; 4]``."""

    element_typ: Final[Typ]
    length: Final[ArrayLength]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "array_typ")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        typ, length = tree.children
        self.element_typ = Typ.from_tree(file, _as_tree(typ))
        self.length = ArrayLength.from_tree(file, _as_tree(length))

    @override
    def diag_str(self) -> str:
        return "array type specifier"


class Param(Ast):
    """A single formal parameter in a function declaration or definition."""

    name: Final[Ident]
    typ: Final[Typ]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "param")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        ident, typ = tree.children
        self.name = Ident.from_tree(file, _as_tree(ident))
        self.typ = Typ.from_tree(file, _as_tree(typ))

    @override
    def diag_str(self) -> str:
        return f'parameter "{self.name.name}"'


class Receiver(Ast):
    """A ``*self`` or ``*mut self`` receiver whose pointee is supplied by its impl."""

    name: Final[Ident]
    mut: Final[Optional[Mutability]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "receiver")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        self.name = Ident._synthetic("self", self.span)
        (mut,) = tree.children
        self.mut = Mutability.from_tree(file, _as_tree(mut))

    @override
    def diag_str(self) -> str:
        return '"self" receiver'


class ComptimeParam(Ast):
    """A generic comptime parameter and its bounds.

    ``bounds`` is parsed the same way regardless of which kind of
    parameter this turns out to be: a type parameter's bounds are trait
    references (``T: Trait1 + Trait2``); a value parameter instead has
    exactly one bound naming its value type (``N: usize``). Which one
    ``bounds`` actually names is only resolved later, once the referenced
    names are in scope - see :func:`leech.typs.comptime_params_from_ast`.
    """

    ident: Final[Ident]
    bounds: Final[tuple[BasicTyp, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "comptime_param")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        ident, *rest = list(map(_as_tree, tree.children))
        self.ident = Ident.from_tree(file, ident)
        if rest:
            (bound_list,) = rest
            self.bounds = tuple(BasicTyp(file, _as_tree(b)) for b in bound_list.children)
        else:
            self.bounds = ()

    @override
    def diag_str(self) -> str:
        return f'generic parameter "{self.ident.name}"'


class Defn(Ast):
    """Base class for every top-level module item definition."""

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Defn:
        """Build the definition selected by a parse tree's grammar rule."""
        asserts.assert_eq(tree.data, "defn")
        (child,) = map(_as_tree, tree.children)
        child_classes = {
            "fn_defn": FnDefn,
            "extern_fn_decl": ExternFnDecl,
            "var_defn": VarDefn,
            "struct_defn": StructDefn,
            "enum_defn": EnumDefn,
            "trait_defn": TraitDefn,
            "impl_defn": ImplDefn,
            "import": Import,
        }
        return child_classes[child.data](file, child)


class FnDecl(Defn):
    """Shared syntax for function declarations, definitions, and trait methods."""

    name: Final[Ident]
    #: Empty for a non-generic function.
    comptime_params: Final[tuple[ComptimeParam, ...]]
    receiver: Final[Optional[Receiver]]
    params: Final[tuple[Param, ...]]
    ret_typ: Final[Optional[Typ]]

    def __init__(
        self,
        file,
        tree: lark.tree.ParseTree,
        ident: lark.tree.ParseTree,
        comptime_params: Optional[lark.tree.ParseTree],
        param_list: lark.tree.ParseTree,
        ret_typ: Optional[lark.tree.ParseTree],
    ) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        self.name = Ident.from_tree(file, ident)
        if comptime_params is not None:
            self.comptime_params = tuple(
                ComptimeParam(file, _as_tree(c)) for c in comptime_params.children
            )
        else:
            self.comptime_params = ()
        self.ret_typ = opt_util.opt_map(ret_typ, lambda x: Typ.from_tree(file, x))

        asserts.assert_eq(param_list.data, "param_list")
        children = [_as_tree(child) for child in param_list.children]
        if children and children[0].data == "receiver":
            self.receiver = Receiver(file, children[0])
            children = children[1:]
        else:
            self.receiver = None
        self.params = tuple(Param(file, child) for child in children)


class ExternFnDecl(FnDecl):
    """An ``extern`` function declared without a body.

    Never generic - an ``extern`` function has no body to monomorphize.
    """

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "extern_fn_decl")
        ident, param_list, ret_typ = tree.children
        super().__init__(
            file,
            tree,
            _as_tree(ident),
            None,
            _as_tree(param_list),
            opt_util.opt_map(ret_typ, _as_tree),
        )

    @override
    def diag_str(self) -> str:
        return "function declaration"


class TraitFnDecl(FnDecl):
    """A non-generic trait method prototype without a default body."""

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "trait_fn_decl")
        ident, param_list, ret_typ = tree.children
        super().__init__(
            file,
            tree,
            _as_tree(ident),
            None,
            _as_tree(param_list),
            opt_util.opt_map(ret_typ, _as_tree),
        )

    @override
    def diag_str(self) -> str:
        return "trait method"


class FnDefn(FnDecl):
    """A function defined with a body."""

    access: Final[Optional[Access]]
    block: Final[BlockExpr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "fn_defn")
        access, ident, comptime_params, param_list, ret_typ, block = tree.children
        super().__init__(
            file,
            tree,
            _as_tree(ident),
            opt_util.opt_map(comptime_params, _as_tree),
            _as_tree(param_list),
            opt_util.opt_map(ret_typ, _as_tree),
        )
        self.access = Access.from_tree(file, _as_tree(access))
        self.block = BlockExpr(file, _as_tree(block))

    @override
    def diag_str(self) -> str:
        return "function definition"


class VarDefn(Defn):
    """A module-level ``let`` variable definition."""

    access: Final[Optional[Access]]
    let_stmt: Final[LetStmt]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "var_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        access, let_stmt = tree.children
        self.access = Access.from_tree(file, _as_tree(access))
        self.let_stmt = LetStmt(file, _as_tree(let_stmt))

    @override
    def diag_str(self) -> str:
        return "variable definition"


class StructDefn(Defn):
    """A struct type definition."""

    access: Final[Optional[Access]]
    ident: Final[Ident]
    #: Empty for a non-generic struct.
    comptime_params: Final[tuple[ComptimeParam, ...]]
    fields: Final[tuple[StructFieldDefn, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "struct_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        access, ident, comptime_params, field_defn_list = tree.children
        self.access = Access.from_tree(file, _as_tree(access))
        self.ident = Ident.from_tree(file, _as_tree(ident))
        if comptime_params is not None:
            self.comptime_params = tuple(
                ComptimeParam(file, _as_tree(c)) for c in _as_tree(comptime_params).children
            )
        else:
            self.comptime_params = ()

        field_defn_list = _as_tree(field_defn_list)
        asserts.assert_eq(field_defn_list.data, "struct_field_defn_list")
        self.fields = tuple(
            StructFieldDefn(file, _as_tree(child)) for child in field_defn_list.children
        )

    @override
    def diag_str(self) -> str:
        return f'struct "{self.ident.name}" definition'


class StructFieldDefn(Ast):
    """A single struct field declaration."""

    access: Final[Optional[Access]]
    mut: Final[Optional[Mutability]]
    ident: Final[Ident]
    typ: Final[Typ]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "struct_field_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        access, mut, ident, typ = map(_as_tree, tree.children)
        self.access = Access.from_tree(file, access)
        self.mut = Mutability.from_tree(file, mut)
        self.ident = Ident.from_tree(file, ident)
        self.typ = Typ.from_tree(file, typ)

    @override
    def diag_str(self) -> str:
        return f'struct field "{self.ident.name}" definition'


class EnumDefn(Defn):
    """An enum type definition."""

    access: Final[Optional[Access]]
    ident: Final[Ident]
    #: The explicit backing integer type in parentheses, if written.
    backing_typ: Final[Optional[Typ]]
    variants: Final[tuple[EnumVariantDefn, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "enum_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        access, ident, backing_typ, variant_list = tree.children
        self.access = Access.from_tree(file, _as_tree(access))
        self.ident = Ident.from_tree(file, _as_tree(ident))
        self.backing_typ = opt_util.opt_map(backing_typ, lambda x: Typ.from_tree(file, _as_tree(x)))

        variant_list = _as_tree(variant_list)
        asserts.assert_eq(variant_list.data, "enum_variant_list")
        self.variants = tuple(
            EnumVariantDefn(file, _as_tree(child)) for child in variant_list.children
        )

    @override
    def diag_str(self) -> str:
        return f'enum "{self.ident.name}" definition'


class EnumVariantDefn(Ast):
    """A single variant declaration within an :class:`EnumDefn`."""

    ident: Final[Ident]
    #: The explicit ``= N`` discriminant's magnitude, if written.
    value: Final[Optional[IntLit]]
    #: Whether the discriminant is negative. Only meaningful when
    #: ``value`` is not ``None``.
    negative: Final[bool]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "enum_variant")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        ident, value = tree.children
        self.ident = Ident.from_tree(file, _as_tree(ident))
        value_tree = opt_util.opt_map(value, _as_tree)
        if value_tree is not None:
            asserts.assert_eq(value_tree.data, "enum_variant_value")
        self.negative = opt_util.opt_or_default(
            opt_util.opt_map(value_tree, lambda vt: vt.children[0] is not None), False
        )
        self.value = opt_util.opt_map(value_tree, lambda vt: IntLit(file, _as_tree(vt.children[1])))

    @override
    def diag_str(self) -> str:
        return f'enum variant "{self.ident.name}" definition'


class TraitDefn(Defn):
    """A trait definition: a named set of method prototypes an ``impl ...
    for ...`` block promises to provide."""

    access: Final[Optional[Access]]
    ident: Final[Ident]
    #: Empty for a non-generic trait.
    comptime_params: Final[tuple[ComptimeParam, ...]]
    fn_decls: Final[tuple[TraitFnDecl, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "trait_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        access, ident, comptime_params, *fn_decl_trees = tree.children
        self.access = Access.from_tree(file, _as_tree(access))
        self.ident = Ident.from_tree(file, _as_tree(ident))
        if comptime_params is not None:
            self.comptime_params = tuple(
                ComptimeParam(file, _as_tree(c)) for c in _as_tree(comptime_params).children
            )
        else:
            self.comptime_params = ()
        self.fn_decls = tuple(
            TraitFnDecl(file, _as_tree(fn_decl_tree)) for fn_decl_tree in fn_decl_trees
        )

    @override
    def diag_str(self) -> str:
        return f'trait "{self.ident.name}" definition'


class ImplDefn(Defn):
    """An inherent impl or trait impl, distinguished by ``for_typ``."""

    #: Empty for a non-generic impl block.
    comptime_params: Final[tuple[ComptimeParam, ...]]
    typ: Final[Typ]
    for_typ: Final[Optional[Typ]]
    fn_defns: Final[tuple[FnDefn, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "impl_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        comptime_params, typ, for_typ, *fn_defn_trees = tree.children
        if comptime_params is not None:
            self.comptime_params = tuple(
                ComptimeParam(file, _as_tree(c)) for c in _as_tree(comptime_params).children
            )
        else:
            self.comptime_params = ()
        self.typ = Typ.from_tree(file, _as_tree(typ))
        self.for_typ = opt_util.opt_map(for_typ, lambda t: Typ.from_tree(file, _as_tree(t)))
        self.fn_defns = tuple(
            FnDefn(file, _as_tree(fn_defn_tree)) for fn_defn_tree in fn_defn_trees
        )

    @override
    def diag_str(self) -> str:
        return "impl block"


class Import(Defn):
    """An ``import some_mod;`` or ``import some_mod::sub_mod;`` statement."""

    path: Final[Path]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "import")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (path,) = map(_as_tree, tree.children)
        self.path = Path(file, path)

    @override
    def diag_str(self) -> str:
        return f'import "{self.path.str()}"'


class Access(Ast):
    """An optional ``pub`` access specifier."""

    value: Final[str]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "access")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (child,) = tree.children
        assert child is not None
        self.value = _as_token(child)

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Optional[Access]:
        """Build an access node, or return ``None`` when ``pub`` is absent."""
        (child,) = tree.children
        if child is not None:
            return Access(file, tree)
        return None

    @override
    def diag_str(self) -> str:
        return "access specifier"


class Mutability(Ast):
    """An optional ``mut`` mutability specifier."""

    value: Final[str]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "mut")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (child,) = tree.children
        self.value = _as_token(child)

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Optional[Mutability]:
        """Build a mutability node, or return ``None`` when ``mut`` is absent."""
        (child,) = tree.children
        if child is not None:
            return Mutability(file, tree)
        return None

    @override
    def diag_str(self) -> str:
        return "mut specifier"


class Mod(Ast):
    """A parsed module: the top-level sequence of definitions in a source file."""

    defns: Final[tuple[Defn, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "mod")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        self.defns = tuple(Defn.from_tree(file, _as_tree(child)) for child in tree.children)

    @override
    def diag_str(self) -> str:
        return "module"


def _opt_ast(obj: Any) -> Optional[Ast]:
    attr = getattr(obj, "ast", None)
    if isinstance(attr, Ast):
        return attr
    return None


def opt_span(obj: Any) -> Optional[src.SrcSpan]:
    """Return the span of ``obj.ast``, or ``None`` when unavailable."""
    return opt_util.opt_map(_opt_ast(obj), lambda x: x.span)
