"""The parsed AST: one node class per grammar rule, built from Lark parse trees."""

import ast as python_ast
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, override

from lark import Token
from lark.tree import Branch, ParseTree, Tree

from leech.asserts import assert_eq, assert_in, checked_cast
from leech.opt_util import opt_map
from leech.signage import SIGNED, UNSIGNED, Signage
from leech.src import SrcFile, SrcSpan
from leech.target import ADDR_SIZE


def as_token(branch: Branch[Token]) -> Token:
    """Assert that a Lark tree child is a leaf token, and return it as such.

    :param branch: The parse-tree child to check.
    :return: ``branch``, statically typed as :class:`~lark.Token`.
    :raises AssertionError: If ``branch`` is not a :class:`~lark.Token`.
    """
    return checked_cast(branch, Token)


def as_tree(branch: Branch[Token]) -> Tree[Token]:
    """Assert that a Lark tree child is itself a subtree, and return it as such.

    :param branch: The parse-tree child to check.
    :return: ``branch``, statically typed as :class:`~lark.Tree`.
    :raises AssertionError: If ``branch`` is not a :class:`~lark.Tree`.
    """
    return checked_cast(branch, Tree)


class Ast(ABC):
    """Base class for every AST node.

    :param span: The node's source location.
    """

    span: SrcSpan

    def __init__(self, span: SrcSpan) -> None:
        self.span = span

    @staticmethod
    def _pretty_child(child_name: str, child_val: Any, indent: str, level: int) -> str:
        match child_val:
            case Ast():
                return f"{indent * level}{child_name}: {child_val.pretty(indent, level)}"
            case list():
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

    @abstractmethod
    def diag_str(self) -> str:
        """A short, human-readable description of this node, for use in diagnostics.

        E.g. ``'variable "x"'`` or ``"if expression"``.
        """


class Ident(Ast):
    """A single identifier."""

    name: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "ident")
        super().__init__(SrcSpan(file, tree.meta))
        self.name = as_token(tree.children[0])

    @staticmethod
    def synthetic(name: str, span: SrcSpan) -> Ident:
        """Construct an :class:`Ident` not backed by a parse tree.

        Used for the implicit ``self`` binding a method's receiver
        introduces (see :class:`Receiver`), which has no ``ident``
        subtree of its own to build one from.

        :param name: The identifier text.
        :param span: The source location to attribute the identifier to.
        :return: The synthesized identifier.
        """
        ident = Ident.__new__(Ident)
        ident.span = span
        ident.name = name
        return ident

    @override
    def diag_str(self) -> str:
        return f"identifier {self.name}"


class Path(Ast):
    """A (possibly ``::``-qualified) sequence of identifiers, e.g. ``some_mod::Foo``."""

    idents: list[Ident]

    @override
    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "path")
        super().__init__(SrcSpan(file, tree.meta))
        self.idents = [Ident(file, as_tree(ident)) for ident in tree.children]

    @override
    def diag_str(self) -> str:
        return f"path {self.str()}"

    def str(self) -> str:
        """This path rendered back as ``::``-separated source text."""
        return "::".join(ident.name for ident in self.idents)


class Expr(Ast):
    """Base class for every expression node."""

    @staticmethod
    def _child_ctors() -> dict[str, Callable[[SrcFile, Tree], Expr]]:
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
    def is_expr_tree(tree: ParseTree) -> bool:
        """Whether ``tree``'s grammar rule is one that produces an :class:`Expr`.

        :param tree: The parse tree to check.
        :return: Whether ``tree`` can be passed to :meth:`from_tree`.
        """
        return tree.data in Expr._child_ctors()

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Expr:
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
    def from_tree(file: SrcFile, tree: ParseTree) -> PlaceExpr:
        """Build the appropriate :class:`PlaceExpr` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`PlaceExpr` subclass is constructed.
        :return: The constructed expression node.
        :raises AssertionError: If ``tree``'s grammar rule doesn't produce
            a :class:`PlaceExpr`.
        """
        return checked_cast(Expr.from_tree(file, tree), PlaceExpr)


class BlockExpr(Expr):
    """A ``{ stmt; ...; tail_expr }`` block expression."""

    stmts: list[Stmt]
    expr: Expr | None

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "block_expr")
        super().__init__(SrcSpan(file, tree.meta))

        children = list(map(as_tree, tree.children))

        if len(children) > 0 and Expr.is_expr_tree(children[-1]):
            stmts = children[0:-1]
            expr = children[-1]
        else:
            stmts = children
            expr = None

        self.stmts = [Stmt.from_tree(file, stmt) for stmt in stmts]

        if expr:
            self.expr = Expr.from_tree(file, expr)
        else:
            self.expr = None

    @override
    def diag_str(self) -> str:
        return "block expression"


class IfExpr(Expr):
    """An ``if (condition) { ... } else { ... }`` expression."""

    condition: Expr
    then: BlockExpr
    els: BlockExpr | None

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "if_expr")
        super().__init__(SrcSpan(file, tree.meta))

        condition, then, *rest = list(map(as_tree, tree.children))
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

    label: Ident | None
    condition: Expr
    block: BlockExpr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "while_expr")
        super().__init__(SrcSpan(file, tree.meta))
        label, condition, block = tree.children
        self.label = opt_map(label, lambda x: Ident(file, as_tree(x)))
        self.condition = Expr.from_tree(file, as_tree(condition))
        self.block = BlockExpr(file, as_tree(block))

    @override
    def diag_str(self) -> str:
        return "while expression"


class StrLit(Expr):
    """A string literal."""

    value: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "str_lit")
        super().__init__(SrcSpan(file, tree.meta))
        (tok,) = tree.children
        self.value = python_ast.literal_eval(as_token(tok))

    @override
    def diag_str(self) -> str:
        return "string literal"


class IntLit(Expr):
    """An integer literal, e.g. ``42``, ``5i8``, or ``3usize``.

    A type suffix, if written, fixes the literal's width and signedness
    (:attr:`explicit_width`, :attr:`explicit_signage`). Without one, the
    type is left open here and chosen during type checking, from the type
    the surrounding context expects (see
    :meth:`leech.typcheck.TypCheck._infer_int_lit_typ`) - see that method
    for where the two are turned into a :class:`~leech.typs.IntTyp`.
    """

    token: Token
    value: int
    explicit_width: int | None
    explicit_signage: Signage | None

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "int_lit")
        super().__init__(SrcSpan(file, tree.meta))
        (token,) = tree.children
        self.token = as_token(token)
        m = re.fullmatch("([0-9]+)(?:([iu])((?:[0-9]+)|size))?", self.token)
        assert m is not None

        if m[2] is not None:
            self.explicit_signage = SIGNED if m[2] == "i" else UNSIGNED
            self.explicit_width = ADDR_SIZE if m[3] == "size" else int(m[3])
        else:
            self.explicit_signage = None
            self.explicit_width = None

        self.value = int(m[1])

    @override
    def diag_str(self) -> str:
        return f'int literal "{self.token}"'


class ArrayLength(Expr):
    """An array type's length, e.g. the ``4`` in ``[i32; 4]``."""

    token: Token
    value: int

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "array_length")
        super().__init__(SrcSpan(file, tree.meta))
        (token,) = tree.children
        self.token = as_token(token)
        self.value = int(self.token)

    @override
    def diag_str(self) -> str:
        return f'array length "{self.token}"'


class BoolLit(Expr):
    """A boolean literal, ``true`` or ``false``."""

    token: Token
    value: bool

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "bool_lit")
        super().__init__(SrcSpan(file, tree.meta))
        (token,) = tree.children
        self.token = as_token(token)
        assert_in(token, {"true", "false"})
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

    path: Path
    generic_args: list[Typ]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        path, generic_args = tree.children
        self.path = Path(file, as_tree(path))
        if generic_args is not None:
            self.generic_args = [
                Typ.from_tree(file, as_tree(c)) for c in as_tree(generic_args).children
            ]
        else:
            self.generic_args = []

    @override
    def diag_str(self) -> str:
        return f'variable "{self.path.str()}"'


class ArrayAccessExpr(PlaceExpr):
    """An array indexing expression, ``array[index]``."""

    array: Expr
    index: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "array_access_expr")
        super().__init__(SrcSpan(file, tree.meta))
        array, index = map(as_tree, tree.children)
        self.array = Expr.from_tree(file, array)
        self.index = Expr.from_tree(file, index)

    @override
    def diag_str(self) -> str:
        return "array access expression"


class ArrayExpr(Expr):
    """An array literal, e.g. ``[1, 2, 3]``."""

    elements: list[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "array_expr")
        super().__init__(SrcSpan(file, tree.meta))
        (arg_list,) = map(as_tree, tree.children)
        assert_eq(arg_list.data, "arg_list")
        self.elements = [Expr.from_tree(file, as_tree(child)) for child in arg_list.children]

    @override
    def diag_str(self) -> str:
        return "array expression"


class StructExpr(Expr):
    """A struct literal, e.g. ``Point { x: 1, y: 2 }``."""

    typ: BasicTyp
    fields: list[StructFieldExpr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "struct_expr")
        super().__init__(SrcSpan(file, tree.meta))
        typ, field_list = map(as_tree, tree.children)
        self.typ = BasicTyp(file, typ)

        assert_eq(field_list.data, "struct_field_expr_list")
        self.fields = [StructFieldExpr(file, as_tree(child)) for child in field_list.children]

    @override
    def diag_str(self) -> str:
        return f'struct "{self.typ.path.str()}" expression'


class StructFieldExpr(Expr):
    """A single ``field: value`` entry within a :class:`StructExpr`."""

    ident: Ident
    value: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "struct_field_expr")
        super().__init__(SrcSpan(file, tree.meta))
        ident, value = map(as_tree, tree.children)
        self.ident = Ident(file, ident)
        self.value = Expr.from_tree(file, value)

    @override
    def diag_str(self) -> str:
        return f'struct field "{self.ident.name}" expression'


class StructAccessExpr(PlaceExpr):
    """A struct field access expression, ``struct.field``."""

    struct: Expr
    field: Ident

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "struct_access_expr")
        super().__init__(SrcSpan(file, tree.meta))
        struct, field = map(as_tree, tree.children)
        self.struct = Expr.from_tree(file, struct)
        self.field = Ident(file, field)

    @override
    def diag_str(self) -> str:
        return "struct access expression"


class DerefExpr(PlaceExpr):
    """A pointer dereference expression, ``ptr.*``."""

    ptr: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "deref_expr")
        super().__init__(SrcSpan(file, tree.meta))
        (ptr,) = map(as_tree, tree.children)
        self.ptr = Expr.from_tree(file, ptr)

    @override
    def diag_str(self) -> str:
        return "dereference expression"


class CallExpr(Expr):
    """A function call expression, ``callee(args...)``."""

    callee: Expr
    args: list[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "call_expr")
        super().__init__(SrcSpan(file, tree.meta))
        callee, arg_list = map(as_tree, tree.children)
        self.callee = Expr.from_tree(file, callee)

        assert_eq(arg_list.data, "arg_list")
        self.args = [Expr.from_tree(file, as_tree(child)) for child in arg_list.children]

    @override
    def diag_str(self) -> str:
        return "call expression"


class Op(Ast):
    """A binary or unary operator token, e.g. ``+`` or ``&``."""

    name: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        assert_eq(len(tree.children), 1)
        self.name = as_token(tree.children[0])

    @override
    def diag_str(self) -> str:
        return f"{self.name} operation"


class BinOpExpr(Expr):
    """A binary operator expression, ``lhs op rhs``."""

    lhs: Expr
    op: Op
    rhs: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        lhs, op, rhs = map(as_tree, tree.children)
        self.lhs = Expr.from_tree(file, lhs)
        self.op = Op(file, op)
        self.rhs = Expr.from_tree(file, rhs)

    @override
    def diag_str(self) -> str:
        return f"binary {self.op.name} operation expression"


class UnaryOpExpr(Expr):
    """A unary operator expression, ``op operand`` (``&operand`` or ``-operand``)."""

    op: Op
    operand: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        op, operand = map(as_tree, tree.children)
        self.op = Op(file, op)
        self.operand = Expr.from_tree(file, operand)

    @override
    def diag_str(self) -> str:
        return f"unary {self.op.name} operation expression"


class Stmt(Ast):
    """Base class for every statement node."""

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Stmt:
        """Build the appropriate :class:`Stmt` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`Stmt` subclass is constructed.
        :return: The constructed statement node.
        """
        assert_eq(tree.data, "stmt")
        (child,) = map(as_tree, tree.children)
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

    expr: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "expr_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        (child,) = tree.children
        self.expr = Expr.from_tree(file, as_tree(child))

    @override
    def diag_str(self) -> str:
        return "expression statement"


class RetStmt(Stmt):
    """A ``return`` statement, with or without a value."""

    expr: Expr | None

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "ret_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        (expr,) = tree.children
        self.expr = opt_map(expr, lambda x: Expr.from_tree(file, as_tree(x)))

    @override
    def diag_str(self) -> str:
        return "return statement"


class BreakStmt(Stmt):
    """A ``break`` statement, with or without a target loop label."""

    label: Ident | None

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "break_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        (label,) = tree.children
        self.label = opt_map(label, lambda x: Ident(file, as_tree(x)))

    @override
    def diag_str(self) -> str:
        return "break statement"


class ContinueStmt(Stmt):
    """A ``continue`` statement, with or without a target loop label."""

    label: Ident | None

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "continue_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        (label,) = tree.children
        self.label = opt_map(label, lambda x: Ident(file, as_tree(x)))

    @override
    def diag_str(self) -> str:
        return "continue statement"


class LetStmt(Stmt):
    """A local ``let`` binding statement, with an optional declared type."""

    mut: Mutability | None
    ident: Ident
    typ: Typ | None
    expr: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "let_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        mut, ident, typ, expr = tree.children
        self.mut = Mutability.from_tree(file, as_tree(mut))
        self.ident = Ident(file, as_tree(ident))
        self.typ = opt_map(typ, lambda x: Typ.from_tree(file, as_tree(x)))
        self.expr = Expr.from_tree(file, as_tree(expr))

    @override
    def diag_str(self) -> str:
        return "let statement"


class AssignmentStmt(Stmt):
    """An assignment statement, ``place = expr``."""

    place: PlaceExpr
    expr: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "assignment_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        place, expr = map(as_tree, tree.children)
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

    def __init__(self, span: SrcSpan) -> None:
        super().__init__(span)
        self._typ = None

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Typ:
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

    path: Path
    generic_args: list[Typ]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "basic_typ")
        super().__init__(SrcSpan(file, tree.meta))
        path, generic_args = tree.children
        self.path = Path(file, as_tree(path))
        if generic_args is not None:
            self.generic_args = [
                Typ.from_tree(file, as_tree(c)) for c in as_tree(generic_args).children
            ]
        else:
            self.generic_args = []

    @override
    def diag_str(self) -> str:
        return f'type specifier "{self.path.str()}"'


class PtrTyp(Typ):
    """A pointer type expression, e.g. ``*i32`` or ``*mut i32``."""

    mut: Mutability | None
    pointee_typ: Typ

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "ptr_typ")
        super().__init__(SrcSpan(file, tree.meta))
        mut, typ = tree.children
        self.mut = Mutability.from_tree(file, as_tree(mut))
        self.pointee_typ = Typ.from_tree(file, as_tree(typ))

    @override
    def diag_str(self) -> str:
        return "pointer type specifier"


class ArrayTyp(Typ):
    """A fixed-length array type expression, e.g. ``[i32; 4]``."""

    element_typ: Typ
    length: ArrayLength

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "array_typ")
        super().__init__(SrcSpan(file, tree.meta))
        typ, length = tree.children
        self.element_typ = Typ.from_tree(file, as_tree(typ))
        self.length = ArrayLength(file, as_tree(length))

    @override
    def diag_str(self) -> str:
        return "array type specifier"


class Param(Ast):
    """A single formal parameter in a function declaration or definition."""

    name: Ident
    typ: Typ

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "param")
        super().__init__(SrcSpan(file, tree.meta))
        ident, typ = tree.children
        self.name = Ident(file, as_tree(ident))
        self.typ = Typ.from_tree(file, as_tree(typ))

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

    name: Ident
    mut: Mutability | None

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "receiver")
        super().__init__(SrcSpan(file, tree.meta))
        self.name = Ident.synthetic("self", self.span)
        (mut,) = tree.children
        self.mut = Mutability.from_tree(file, as_tree(mut))

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

    ident: Ident
    bounds: list[Path]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "generic_param")
        super().__init__(SrcSpan(file, tree.meta))
        ident, *rest = list(map(as_tree, tree.children))
        self.ident = Ident(file, ident)
        if rest:
            (bound_list,) = rest
            self.bounds = [Path(file, as_tree(p)) for p in bound_list.children]
        else:
            self.bounds = []

    @override
    def diag_str(self) -> str:
        return f'generic parameter "{self.ident.name}"'


class Defn(Ast):
    """Base class for every top-level module item definition."""

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Defn:
        """Build the appropriate :class:`Defn` subclass instance from a parse tree.

        :param file: The source file ``tree`` was parsed from.
        :param tree: The parse tree, whose grammar rule determines which
            :class:`Defn` subclass is constructed.
        :return: The constructed definition node.
        """
        assert_eq(tree.data, "defn")
        (child,) = map(as_tree, tree.children)
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

    name: Ident
    #: Empty for a non-generic function.
    generic_params: list[GenericParam]
    receiver: Receiver | None
    params: list[Param]
    ret_typ: Typ | None

    def __init__(
        self,
        file,
        tree: ParseTree,
        ident: ParseTree,
        generic_params: ParseTree | None,
        param_list: ParseTree,
        ret_typ: ParseTree | None,
    ) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        self.name = Ident(file, ident)
        if generic_params is not None:
            self.generic_params = [GenericParam(file, as_tree(c)) for c in generic_params.children]
        else:
            self.generic_params = []
        self.ret_typ = opt_map(ret_typ, lambda x: Typ.from_tree(file, x))

        assert_eq(param_list.data, "param_list")
        children = [as_tree(child) for child in param_list.children]
        if children and children[0].data == "receiver":
            self.receiver = Receiver(file, children[0])
            children = children[1:]
        else:
            self.receiver = None
        self.params = [Param(file, child) for child in children]


class FnDecl(FnSpec):
    """A function declared without a body (e.g. an external/builtin function).

    Never generic - an ``extern`` function has no body to monomorphize.
    """

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "fn_decl")
        ident, param_list, ret_typ = tree.children
        super().__init__(
            file, tree, as_tree(ident), None, as_tree(param_list), opt_map(ret_typ, as_tree)
        )

    @override
    def diag_str(self) -> str:
        return "function declaration"


class TraitFn(FnSpec):
    """A method prototype declared inside a :class:`TraitDefn`.

    Never generic itself - same restriction as an associated function (see
    :meth:`~leech.ir_module.Mod._build_impl_defn`'s
    ``generic associated functions aren't supported yet`` assertion), which
    this shares the shape of. Has no body: a trait method is a promise every
    implementing type keeps, not itself an implementation - default bodies
    are deferred.
    """

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "trait_fn")
        ident, param_list, ret_typ = tree.children
        super().__init__(
            file, tree, as_tree(ident), None, as_tree(param_list), opt_map(ret_typ, as_tree)
        )

    @override
    def diag_str(self) -> str:
        return "trait method"


class FnDefn(FnSpec):
    """A function defined with a body."""

    access: Access | None
    block: BlockExpr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "fn_defn")
        access, ident, generic_params, param_list, ret_typ, block = tree.children
        super().__init__(
            file,
            tree,
            as_tree(ident),
            opt_map(generic_params, as_tree),
            as_tree(param_list),
            opt_map(ret_typ, as_tree),
        )
        self.access = Access.from_tree(file, as_tree(access))
        self.block = BlockExpr(file, as_tree(block))

    @override
    def diag_str(self) -> str:
        return "function definition"


class VarDefn(Defn):
    """A module-level ``let`` variable definition."""

    access: Access | None
    let_stmt: LetStmt

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "var_defn")
        super().__init__(SrcSpan(file, tree.meta))
        access, let_stmt = tree.children
        self.access = Access.from_tree(file, as_tree(access))
        self.let_stmt = LetStmt(file, as_tree(let_stmt))

    @override
    def diag_str(self) -> str:
        return "variable definition"


class StructDefn(Defn):
    """A struct type definition."""

    access: Access | None
    ident: Ident
    #: Empty for a non-generic struct.
    generic_params: list[GenericParam]
    fields: list[StructFieldDefn]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "struct_defn")
        super().__init__(SrcSpan(file, tree.meta))
        access, ident, generic_params, field_defn_list = tree.children
        self.access = Access.from_tree(file, as_tree(access))
        self.ident = Ident(file, as_tree(ident))
        if generic_params is not None:
            self.generic_params = [
                GenericParam(file, as_tree(c)) for c in as_tree(generic_params).children
            ]
        else:
            self.generic_params = []

        field_defn_list = as_tree(field_defn_list)
        assert_eq(field_defn_list.data, "struct_field_defn_list")
        self.fields = [StructFieldDefn(file, as_tree(child)) for child in field_defn_list.children]

    @override
    def diag_str(self) -> str:
        return f'struct "{self.ident.name}" definition'


class StructFieldDefn(Ast):
    """A single field declaration within a :class:`StructDefn`."""

    access: Access | None
    mut: Mutability | None
    ident: Ident
    typ: Typ

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "struct_field_defn")
        super().__init__(SrcSpan(file, tree.meta))
        access, mut, ident, typ = map(as_tree, tree.children)
        self.access = Access.from_tree(file, access)
        self.mut = Mutability.from_tree(file, mut)
        self.ident = Ident(file, ident)
        self.typ = Typ.from_tree(file, typ)

    @override
    def diag_str(self) -> str:
        return f'struct field "{self.ident.name}" definition'


class TraitDefn(Defn):
    """A trait definition: a named set of method prototypes an ``impl ...
    for ...`` block promises to provide."""

    access: Access | None
    ident: Ident
    #: Empty for a non-generic trait.
    generic_params: list[GenericParam]
    methods: list[TraitFn]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "trait_defn")
        super().__init__(SrcSpan(file, tree.meta))
        access, ident, generic_params, *method_trees = tree.children
        self.access = Access.from_tree(file, as_tree(access))
        self.ident = Ident(file, as_tree(ident))
        if generic_params is not None:
            self.generic_params = [
                GenericParam(file, as_tree(c)) for c in as_tree(generic_params).children
            ]
        else:
            self.generic_params = []
        self.methods = [TraitFn(file, as_tree(m)) for m in method_trees]

    @override
    def diag_str(self) -> str:
        return f'trait "{self.ident.name}" definition'


class ImplDefn(Defn):
    """An ``impl SomeStruct { ... }`` block of associated functions, or,
    if ``for_typ`` is present, an ``impl Trait for SomeTyp { ... }``
    trait implementation - see
    :meth:`~leech.ir_module.Mod._build_inherent_impl_defn` and
    :meth:`~leech.ir_module.Mod._build_trait_impl_defn` respectively.

    :param for_typ: The parsed ``for`` clause's type, or ``None`` for an
        inherent impl.
    """

    #: Empty for a non-generic impl block.
    generic_params: list[GenericParam]
    typ: Typ
    for_typ: Typ | None
    fns: list[FnDefn]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "impl_defn")
        super().__init__(SrcSpan(file, tree.meta))
        generic_params, typ, for_typ, *fn_trees = tree.children
        if generic_params is not None:
            self.generic_params = [
                GenericParam(file, as_tree(c)) for c in as_tree(generic_params).children
            ]
        else:
            self.generic_params = []
        self.typ = Typ.from_tree(file, as_tree(typ))
        self.for_typ = opt_map(for_typ, lambda t: Typ.from_tree(file, as_tree(t)))
        self.fns = [FnDefn(file, as_tree(fn_tree)) for fn_tree in fn_trees]

    @override
    def diag_str(self) -> str:
        return "impl block"


class Import(Defn):
    """An ``import some_mod;`` statement."""

    ident: Ident

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "import")
        super().__init__(SrcSpan(file, tree.meta))
        (ident,) = map(as_tree, tree.children)
        self.ident = Ident(file, ident)

    @override
    def diag_str(self) -> str:
        return f'import "{self.ident.name}"'


class Access(Ast):
    """An optional ``pub`` access specifier."""

    value: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "access")
        super().__init__(SrcSpan(file, tree.meta))
        (child,) = tree.children
        assert child is not None
        self.value = as_token(child)

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Access | None:
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

    value: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "mut")
        super().__init__(SrcSpan(file, tree.meta))
        (child,) = tree.children
        self.value = as_token(child)

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Mutability | None:
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

    defns: list[Defn]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "mod")
        super().__init__(SrcSpan(file, tree.meta))
        self.defns = [Defn.from_tree(file, as_tree(child)) for child in tree.children]

    @override
    def diag_str(self) -> str:
        return "module"


def opt_ast(obj: Any) -> Ast | None:
    """Get ``obj``'s ``ast`` attribute, if it has one and it's an :class:`Ast`.

    :param obj: The object to inspect (typically an
        :class:`~leech.ir_values.Value`).
    :return: ``obj.ast``, or ``None`` if absent or not an :class:`Ast`.
    """
    attr = getattr(obj, "ast", None)
    if isinstance(attr, Ast):
        return attr
    return None


def opt_span(obj: Any) -> SrcSpan | None:
    """Get the source span of ``obj``'s ``ast`` attribute, if it has one.

    :param obj: The object to inspect (typically an
        :class:`~leech.ir_values.Value`).
    :return: The span of ``obj.ast``, or ``None`` if unavailable.
    """
    return opt_map(opt_ast(obj), lambda x: x.span)
