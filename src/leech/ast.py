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
    """Assert that a Lark tree child is a leaf token, and return it as such.

    :param branch: The parse-tree child to check.
    :return: ``branch``, statically typed as :class:`~lark.Token`.
    :raises AssertionError: If ``branch`` is not a :class:`~lark.Token`.
    """
    return asserts.checked_cast(branch, lark.Token)


def _as_tree(branch: lark.tree.Branch[lark.Token]) -> lark.tree.Tree[lark.Token]:
    """Assert that a Lark tree child is itself a subtree, and return it as such.

    :param branch: The parse-tree child to check.
    :return: ``branch``, statically typed as :class:`~lark.Tree`.
    :raises AssertionError: If ``branch`` is not a :class:`~lark.Tree`.
    """
    return asserts.checked_cast(branch, lark.tree.Tree)


class Ast(abc.ABC):
    """Base class for every AST node.

    :param span: The node's source location.
    """

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
        """Render this node and its children as an indented tree, for debugging.

        :param indent: The string used for one level of indentation.
        :param level: The starting indentation depth.
        :return: The formatted tree.
        """
        cls_name = type(self).__name__
        child_attrs = ((k, v) for k, v in vars(self).items() if k != "span")
        child_strs = "\n".join(Ast._pretty_child(k, v, indent, level + 1) for k, v in child_attrs)
        return f"{cls_name}\n{child_strs}"

    @abc.abstractmethod
    def diag_str(self) -> str:
        """A short, human-readable description of this node, for use in diagnostics.

        E.g. ``'variable "x"'`` or ``"if expression"``.
        """


class Ident(Ast):
    """A single identifier."""

    name: Final[str]

    def __init__(self, span: src.SrcSpan, name: str) -> None:
        super().__init__(span)
        self.name = name

    @classmethod
    def from_tree(cls, file: src.SrcFile, tree: lark.tree.ParseTree) -> Ident:
        """Build an :class:`Ident` from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree to build from.
        :return: The constructed identifier.
        """
        asserts.assert_eq(tree.data, "ident")
        span = src.SrcSpan.from_lark_meta(file, tree.meta)
        return cls(span, _as_token(tree.children[0]))

    @staticmethod
    def _synthetic(name: str, span: src.SrcSpan) -> Ident:
        """Construct an :class:`Ident` not backed by a parse tree.

        Used for the implicit ``self`` binding a method's receiver
        introduces (see :class:`Receiver`), which has no ``ident``
        subtree of its own to build one from.

        :param name: The identifier text.
        :param span: The source location to attribute the identifier to.
        :return: The synthesized identifier.
        """
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
            "struct_access_expr": StructAccessExpr,
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
        """Whether ``tree``'s grammar rule is one that produces an :class:`Expr`.

        :param tree: The parse tree to check.
        :return: Whether ``tree`` can be passed to :meth:`from_tree`.
        """
        return tree.data in Expr._child_ctors()

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Expr:
        """Build the appropriate :class:`Expr` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`Expr` subclass is constructed.
        :return: The constructed expression node.
        """
        return Expr._child_ctors()[tree.data](file, tree)


class PlaceExpr(Expr):
    """Base class for expressions that can appear on the left of ``=``."""

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> PlaceExpr:
        """Build the appropriate :class:`PlaceExpr` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`PlaceExpr` subclass is constructed.
        :return: The constructed expression node.
        :raises AssertionError: If ``tree``'s grammar rule doesn't produce
            a :class:`PlaceExpr`.
        """
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

        self.stmts = tuple(Stmt.from_tree(file, stmt) for stmt in stmts)

        if expr:
            self.expr = Expr.from_tree(file, expr)
        else:
            self.expr = None

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
    """An integer literal, e.g. ``42``, ``5i8``, or ``3usize``.

    A type suffix, if written, fixes the literal's width and signedness
    (:attr:`explicit_width`, :attr:`explicit_signage`). Without one, the
    type is left open here and chosen during type checking, from the type
    the surrounding context expects, where the two are turned into a
    :class:`~leech.typs.IntTyp`.
    """

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


class ArrayLength(Expr):
    """An array type's length, e.g. the ``4`` in ``[i32; 4]``."""

    token: Final[lark.Token]
    value: Final[int]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "array_length")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (token,) = tree.children
        self.token = _as_token(token)
        self.value = int(self.token)

    @override
    def diag_str(self) -> str:
        return f'array length "{self.token}"'


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

    ``generic_args`` holds an explicit type application, e.g. the ``[i32]``
    in ``f[i32](x)``; empty when the callee's type parameters (if any) are
    left to be inferred.
    """

    path: Final[Path]
    generic_args: Final[tuple[Typ, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        path, generic_args = tree.children
        self.path = Path(file, _as_tree(path))
        if generic_args is not None:
            self.generic_args = tuple(
                Typ.from_tree(file, _as_tree(c)) for c in _as_tree(generic_args).children
            )
        else:
            self.generic_args = ()

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
    """A single ``field: value`` entry within a :class:`StructExpr`."""

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


class StructAccessExpr(PlaceExpr):
    """A struct field access expression, ``struct.field``."""

    struct: Final[Expr]
    field: Final[Ident]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "struct_access_expr")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        struct, field = map(_as_tree, tree.children)
        self.struct = Expr.from_tree(file, struct)
        self.field = Ident.from_tree(file, field)

    @override
    def diag_str(self) -> str:
        return "struct access expression"


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
        """Build the appropriate :class:`Stmt` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`Stmt` subclass is constructed.
        :return: The constructed statement node.
        """
        asserts.assert_eq(tree.data, "stmt")
        (child,) = map(_as_tree, tree.children)
        child_classes = {
            "expr_stmt": ExprStmt,
            "ret_stmt": RetStmt,
            "let_stmt": LetStmt,
            "assignment_stmt": AssignmentStmt,
            "break_stmt": BreakStmt,
            "continue_stmt": ContinueStmt,
        }
        return child_classes[child.data](file, child)


class ExprStmt(Stmt):
    """An expression evaluated for its side effects, followed by ``;``."""

    expr: Final[Expr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "expr_stmt")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (child,) = tree.children
        self.expr = Expr.from_tree(file, _as_tree(child))

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
    """Base class for every parsed type-expression node.

    Not to be confused with :class:`leech.typs.Typ`, the *resolved* type
    representation this is later converted to (see
    :meth:`leech.typs.Typ.from_ast`).
    """

    def __init__(self, span: src.SrcSpan) -> None:
        super().__init__(span)
        self._typ = None

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Typ:
        """Build the appropriate :class:`Typ` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`Typ` subclass is constructed.
        :return: The constructed type-expression node.
        """
        child_classes = {
            "basic_typ": BasicTyp,
            "ptr_typ": PtrTyp,
            "array_typ": ArrayTyp,
        }
        return child_classes[tree.data](file, tree)


class BasicTyp(Typ):
    """A named type, e.g. ``i32`` or ``some_mod::Foo``, optionally applied to
    generic arguments, e.g. ``Vec[i32]``. ``generic_args`` is empty for a
    non-generic type.
    """

    path: Final[Path]
    generic_args: Final[tuple[Typ, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "basic_typ")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        path, generic_args = tree.children
        self.path = Path(file, _as_tree(path))
        if generic_args is not None:
            self.generic_args = tuple(
                Typ.from_tree(file, _as_tree(c)) for c in _as_tree(generic_args).children
            )
        else:
            self.generic_args = ()

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
        self.length = ArrayLength(file, _as_tree(length))

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
    """A method's ``self`` receiver: ``*self`` or ``*mut self``.

    Unlike :class:`Param`, its type isn't written in source - it's always
    a pointer to the enclosing ``impl`` block's struct (see
    :class:`~leech.ir_module.NonBuiltinFnSpec`), so there is no ``typ`` field
    here, only the pointer's mutability.
    """

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


class GenericParam(Ast):
    """A single type parameter in a generic item's parameter list, e.g. the
    ``T`` in ``fn id[T](x: T) T``, or the ``T: Show`` in
    ``fn show_it[T: Show](x: T)``.

    ``bounds`` parses now but means nothing until traits exist to check it
    against.
    """

    ident: Final[Ident]
    bounds: Final[tuple[Path, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "generic_param")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        ident, *rest = list(map(_as_tree, tree.children))
        self.ident = Ident.from_tree(file, ident)
        if rest:
            (bound_list,) = rest
            self.bounds = tuple(Path(file, _as_tree(p)) for p in bound_list.children)
        else:
            self.bounds = ()

    @override
    def diag_str(self) -> str:
        return f'generic parameter "{self.ident.name}"'


class Defn(Ast):
    """Base class for every top-level module item definition."""

    @staticmethod
    def from_tree(file: src.SrcFile, tree: lark.tree.ParseTree) -> Defn:
        """Build the appropriate :class:`Defn` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`Defn` subclass is constructed.
        :return: The constructed definition node.
        """
        asserts.assert_eq(tree.data, "defn")
        (child,) = map(_as_tree, tree.children)
        child_classes = {
            "fn_defn": FnDefn,
            "fn_decl": FnDecl,
            "var_defn": VarDefn,
            "struct_defn": StructDefn,
            "trait_defn": TraitDefn,
            "impl_defn": ImplDefn,
            "import": Import,
        }
        return child_classes[child.data](file, child)


class FnSpec(Defn):
    """Base class shared by function declarations and function definitions.

    :param file: The source file ``tree`` was parsed from.
    :param tree: The declaration's or definition's own parse tree, used
        only for its source span.
    :param ident: The parse tree for the function's name.
    :param generic_params: The parse tree for the function's ``[...]``
        type parameter list, or ``None`` if it has none - always ``None``
        for a :class:`FnDecl`, since an ``extern`` function can't be
        generic.
    :param param_list: The parse tree for the function's parameter list.
    :param ret_typ: The parse tree for the function's return type, or
        ``None`` if unspecified (i.e. ``void``).
    """

    name: Final[Ident]
    #: Empty for a non-generic function.
    generic_params: Final[tuple[GenericParam, ...]]
    receiver: Final[Optional[Receiver]]
    params: Final[tuple[Param, ...]]
    ret_typ: Final[Optional[Typ]]

    def __init__(
        self,
        file,
        tree: lark.tree.ParseTree,
        ident: lark.tree.ParseTree,
        generic_params: Optional[lark.tree.ParseTree],
        param_list: lark.tree.ParseTree,
        ret_typ: Optional[lark.tree.ParseTree],
    ) -> None:
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        self.name = Ident.from_tree(file, ident)
        if generic_params is not None:
            self.generic_params = tuple(
                GenericParam(file, _as_tree(c)) for c in generic_params.children
            )
        else:
            self.generic_params = ()
        self.ret_typ = opt_util.opt_map(ret_typ, lambda x: Typ.from_tree(file, x))

        asserts.assert_eq(param_list.data, "param_list")
        children = [_as_tree(child) for child in param_list.children]
        if children and children[0].data == "receiver":
            self.receiver = Receiver(file, children[0])
            children = children[1:]
        else:
            self.receiver = None
        self.params = tuple(Param(file, child) for child in children)


class FnDecl(FnSpec):
    """A function declared without a body (e.g. an external/builtin function).

    Never generic - an ``extern`` function has no body to monomorphize.
    """

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "fn_decl")
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


class TraitFn(FnSpec):
    """A method prototype declared inside a :class:`TraitDefn`.

    Never generic itself - same restriction as an associated function,
    which this shares the shape of: generic associated functions aren't
    supported yet. Has no body: a trait method is a promise every
    implementing type keeps, not itself an implementation - default bodies
    are deferred.
    """

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "trait_fn")
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


class FnDefn(FnSpec):
    """A function defined with a body."""

    access: Final[Optional[Access]]
    block: Final[BlockExpr]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "fn_defn")
        access, ident, generic_params, param_list, ret_typ, block = tree.children
        super().__init__(
            file,
            tree,
            _as_tree(ident),
            opt_util.opt_map(generic_params, _as_tree),
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
    generic_params: Final[tuple[GenericParam, ...]]
    fields: Final[tuple[StructFieldDefn, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "struct_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        access, ident, generic_params, field_defn_list = tree.children
        self.access = Access.from_tree(file, _as_tree(access))
        self.ident = Ident.from_tree(file, _as_tree(ident))
        if generic_params is not None:
            self.generic_params = tuple(
                GenericParam(file, _as_tree(c)) for c in _as_tree(generic_params).children
            )
        else:
            self.generic_params = ()

        field_defn_list = _as_tree(field_defn_list)
        asserts.assert_eq(field_defn_list.data, "struct_field_defn_list")
        self.fields = tuple(
            StructFieldDefn(file, _as_tree(child)) for child in field_defn_list.children
        )

    @override
    def diag_str(self) -> str:
        return f'struct "{self.ident.name}" definition'


class StructFieldDefn(Ast):
    """A single field declaration within a :class:`StructDefn`."""

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


class TraitDefn(Defn):
    """A trait definition: a named set of method prototypes an ``impl ...
    for ...`` block promises to provide."""

    access: Final[Optional[Access]]
    ident: Final[Ident]
    #: Empty for a non-generic trait.
    generic_params: Final[tuple[GenericParam, ...]]
    methods: Final[tuple[TraitFn, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "trait_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        access, ident, generic_params, *method_trees = tree.children
        self.access = Access.from_tree(file, _as_tree(access))
        self.ident = Ident.from_tree(file, _as_tree(ident))
        if generic_params is not None:
            self.generic_params = tuple(
                GenericParam(file, _as_tree(c)) for c in _as_tree(generic_params).children
            )
        else:
            self.generic_params = ()
        self.methods = tuple(TraitFn(file, _as_tree(m)) for m in method_trees)

    @override
    def diag_str(self) -> str:
        return f'trait "{self.ident.name}" definition'


class ImplDefn(Defn):
    """An ``impl SomeStruct { ... }`` block of associated functions, or,
    if ``for_typ`` is present, an ``impl Trait for SomeTyp { ... }``
    trait implementation.

    :param for_typ: The parsed ``for`` clause's type, or ``None`` for an
        inherent impl.
    """

    #: Empty for a non-generic impl block.
    generic_params: Final[tuple[GenericParam, ...]]
    typ: Final[Typ]
    for_typ: Final[Optional[Typ]]
    fns: Final[tuple[FnDefn, ...]]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "impl_defn")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        generic_params, typ, for_typ, *fn_trees = tree.children
        if generic_params is not None:
            self.generic_params = tuple(
                GenericParam(file, _as_tree(c)) for c in _as_tree(generic_params).children
            )
        else:
            self.generic_params = ()
        self.typ = Typ.from_tree(file, _as_tree(typ))
        self.for_typ = opt_util.opt_map(for_typ, lambda t: Typ.from_tree(file, _as_tree(t)))
        self.fns = tuple(FnDefn(file, _as_tree(fn_tree)) for fn_tree in fn_trees)

    @override
    def diag_str(self) -> str:
        return "impl block"


class Import(Defn):
    """An ``import some_mod;`` statement."""

    ident: Final[Ident]

    def __init__(self, file: src.SrcFile, tree: lark.tree.ParseTree) -> None:
        asserts.assert_eq(tree.data, "import")
        super().__init__(src.SrcSpan.from_lark_meta(file, tree.meta))
        (ident,) = map(_as_tree, tree.children)
        self.ident = Ident.from_tree(file, ident)

    @override
    def diag_str(self) -> str:
        return f'import "{self.ident.name}"'


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
        """Build an :class:`Access` from a parse tree, if it has one present.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree for the (possibly absent) access
            specifier.
        :return: The constructed node, or ``None`` if no ``pub`` keyword
            is present.
        """
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
        """Build a :class:`Mutability` from a parse tree, if it has one present.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree for the (possibly absent) mutability
            specifier.
        :return: The constructed node, or ``None`` if no ``mut`` keyword
            is present.
        """
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
    """Get ``obj``'s ``ast`` attribute, if it has one and it's an :class:`Ast`.

    :param obj: The object to inspect (typically an
        :class:`~leech.ir_values.Value`).
    :return: ``obj.ast``, or ``None`` if absent or not an :class:`Ast`.
    """
    attr = getattr(obj, "ast", None)
    if isinstance(attr, Ast):
        return attr
    return None


def opt_span(obj: Any) -> Optional[src.SrcSpan]:
    """Get the source span of ``obj``'s ``ast`` attribute, if it has one.

    :param obj: The object to inspect (typically an
        :class:`~leech.ir_values.Value`).
    :return: The span of ``obj.ast``, or ``None`` if unavailable.
    """
    return opt_util.opt_map(_opt_ast(obj), lambda x: x.span)
