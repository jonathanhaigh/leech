from abc import ABC, abstractmethod
import ast as python_ast
from collections.abc import Callable
import re
from typing import Any, Optional, override

from lark import Token
from lark.tree import Branch, ParseTree, Tree

from l0 import typs
from l0.asserts import assert_eq, assert_in, checked_cast
from l0.opt_util import opt_map
from l0.src import SrcFile, SrcSpan
from l0.typs import ADDR_SIZE, SIGNED, UNSIGNED


def as_token(branch: Branch[Token]) -> Token:
    return checked_cast(branch, Token)


def as_tree(branch: Branch[Token]) -> Tree[Token]:
    return checked_cast(branch, Tree)


class Ast(ABC):
    span: SrcSpan

    def __init__(self, span: SrcSpan) -> None:
        self.span = span

    @staticmethod
    def _pretty_child(child_name: str, child_val: Any, indent: str, level: int) -> str:
        match child_val:
            case Ast():
                return (
                    f"{indent * level}{child_name}: {child_val.pretty(indent, level)}"
                )
            case list():
                next_level = level + 1
                elts = "".join(
                    f"\n{indent * next_level}{x.pretty(indent, level + 1)}"
                    for x in child_val
                )
                return f"{indent * level}{child_name}:{elts}"
            case _:
                return f"{indent * level}{child_val!r}"

    def pretty(self, indent: str = "  ", level: int = 0) -> str:
        cls_name = type(self).__name__
        child_attrs = ((k, v) for k, v in vars(self).items() if k != "span")
        child_strs = "\n".join(
            Ast._pretty_child(k, v, indent, level + 1) for k, v in child_attrs
        )
        return f"{cls_name}\n{child_strs}"

    @abstractmethod
    def diag_str(self) -> str:
        pass


class Ident(Ast):
    name: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "ident")
        super().__init__(SrcSpan(file, tree.meta))
        self.name = as_token(tree.children[0])

    @override
    def diag_str(self) -> str:
        return f"identifier {self.name}"


class Path(Ast):
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
        return "::".join(ident.name for ident in self.idents)


class Expr(Ast):
    @staticmethod
    def _child_ctors() -> dict["str", Callable[[SrcFile, Tree], Expr]]:
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
            "cmp_expr": BinOpExpr,
            "arith_expr": BinOpExpr,
            "term": BinOpExpr,
            "factor": UnaryOpExpr,
        }

    @staticmethod
    def is_expr_tree(tree: ParseTree) -> bool:
        return tree.data in Expr._child_ctors()

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Expr:
        return Expr._child_ctors()[tree.data](file, tree)


class PlaceExpr(Expr):
    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> PlaceExpr:
        return checked_cast(Expr.from_tree(file, tree), PlaceExpr)


class BlockExpr(Expr):
    stmts: list[Stmt]
    expr: Optional[Expr]

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
    condition: Expr
    then: BlockExpr
    els: Optional[BlockExpr]

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
    condition: Expr
    block: BlockExpr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "while_expr")
        super().__init__(SrcSpan(file, tree.meta))
        condition, block = tree.children
        self.condition = Expr.from_tree(file, as_tree(condition))
        self.block = BlockExpr(file, as_tree(block))

    @override
    def diag_str(self) -> str:
        return "while expression"


class StrLit(Expr):
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
    token: Token
    value: int
    typ: typs.IntTyp

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "int_lit")
        super().__init__(SrcSpan(file, tree.meta))
        (token,) = tree.children
        self.token = as_token(token)
        m = re.fullmatch("([0-9]+)(?:([iu])((?:[0-9]+)|size))?", self.token)
        assert m is not None

        if m[2] is not None:
            signage = SIGNED if m[2] == "i" else UNSIGNED
            if m[3] == "size":
                width = ADDR_SIZE
            else:
                width = int(m[3])
        else:
            signage = SIGNED
            width = 32

        self.typ = typs.IntTyp.get_or_create(width, signage)
        self.value = int(m[1])

    @override
    def diag_str(self) -> str:
        return f'int literal "{self.token}"'


class ArrayLength(Expr):
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
    token: Token
    value: bool

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "bool_lit")
        super().__init__(SrcSpan(file, tree.meta))
        (token,) = tree.children
        self.token = as_token(token)
        assert_in(token, {"true", "false"})
        self.value = True if token == "true" else False

    @override
    def diag_str(self) -> str:
        return f'bool literal "{self.token}"'


class VarExpr(PlaceExpr):
    path: Path

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        (path,) = tree.children
        self.path = Path(file, as_tree(path))

    @override
    def diag_str(self) -> str:
        return f'variable "{self.path.str()}"'


class ArrayAccessExpr(PlaceExpr):
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
    elements: list[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "array_expr")
        super().__init__(SrcSpan(file, tree.meta))
        (arg_list,) = map(as_tree, tree.children)
        assert_eq(arg_list.data, "arg_list")
        self.elements = [
            Expr.from_tree(file, as_tree(child)) for child in arg_list.children
        ]

    @override
    def diag_str(self) -> str:
        return "array expression"


class StructExpr(Expr):
    typ: BasicTyp
    fields: list[StructFieldExpr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "struct_expr")
        super().__init__(SrcSpan(file, tree.meta))
        typ, field_list = map(as_tree, tree.children)
        self.typ = BasicTyp(file, typ)

        assert_eq(field_list.data, "struct_field_expr_list")
        self.fields = [
            StructFieldExpr(file, as_tree(child)) for child in field_list.children
        ]

    @override
    def diag_str(self) -> str:
        return f'struct "{self.typ.path.str()}" expression'


class StructFieldExpr(Expr):
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
    callee: Expr
    args: list[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "call_expr")
        super().__init__(SrcSpan(file, tree.meta))
        callee, arg_list = map(as_tree, tree.children)
        self.callee = Expr.from_tree(file, callee)

        assert_eq(arg_list.data, "arg_list")
        self.args = [
            Expr.from_tree(file, as_tree(child)) for child in arg_list.children
        ]

    @override
    def diag_str(self) -> str:
        return "call expression"


class Op(Ast):
    name: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        assert_eq(len(tree.children), 1)
        self.name = as_token(tree.children[0])

    @override
    def diag_str(self) -> str:
        return f"{self.name} operation"


class BinOpExpr(Expr):
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
    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Stmt:
        assert_eq(tree.data, "stmt")
        (child,) = map(as_tree, tree.children)
        child_classes = {
            "expr_stmt": ExprStmt,
            "ret_stmt": RetStmt,
            "let_stmt": LetStmt,
            "assignment_stmt": AssignmentStmt,
        }
        return child_classes[child.data](file, child)


class ExprStmt(Stmt):
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
    expr: Optional[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "ret_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        (expr,) = tree.children
        self.expr = opt_map(expr, lambda x: Expr.from_tree(file, as_tree(x)))

    @override
    def diag_str(self) -> str:
        return "return statement"


class LetStmt(Stmt):
    mut: Optional[Mutability]
    ident: Ident
    expr: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "let_stmt")
        super().__init__(SrcSpan(file, tree.meta))
        mut, ident, expr = tree.children
        self.mut = Mutability.from_tree(file, as_tree(mut))
        self.ident = Ident(file, as_tree(ident))
        self.expr = Expr.from_tree(file, as_tree(expr))

    @override
    def diag_str(self) -> str:
        return "let statement"


class AssignmentStmt(Stmt):
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
    def __init__(self, span: SrcSpan) -> None:
        super().__init__(span)
        self._typ = None

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Typ:
        child_classes = {
            "basic_typ": BasicTyp,
            "ptr_typ": PtrTyp,
            "array_typ": ArrayTyp,
        }
        return child_classes[tree.data](file, tree)


class BasicTyp(Typ):
    path: Path

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "basic_typ")
        super().__init__(SrcSpan(file, tree.meta))
        (path,) = tree.children
        self.path = Path(file, as_tree(path))

    @override
    def diag_str(self) -> str:
        return f'type specifier "{self.path.str()}"'


class PtrTyp(Typ):
    pointee_typ: Typ

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "ptr_typ")
        super().__init__(SrcSpan(file, tree.meta))
        (typ,) = tree.children
        self.pointee_typ = Typ.from_tree(file, as_tree(typ))

    @override
    def diag_str(self) -> str:
        return "pointer type specifier"


class ArrayTyp(Typ):
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


class Defn(Ast):
    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Defn:
        assert_eq(tree.data, "defn")
        (child,) = map(as_tree, tree.children)
        child_classes = {
            "fn_defn": FnDefn,
            "fn_decl": FnDecl,
            "var_defn": VarDefn,
            "struct_defn": StructDefn,
            "import": Import,
        }
        return child_classes[child.data](file, child)


class FnSpec(Defn):
    name: Ident
    params: list[Param]
    ret_typ: Optional[Typ]

    def __init__(
        self,
        file,
        tree: ParseTree,
        ident: ParseTree,
        param_list: ParseTree,
        ret_typ: Optional[ParseTree],
    ) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        self.name = Ident(file, ident)
        self.ret_typ = opt_map(ret_typ, lambda x: Typ.from_tree(file, x))

        assert_eq(param_list.data, "param_list")
        self.params = [Param(file, as_tree(child)) for child in param_list.children]


class FnDecl(FnSpec):
    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "fn_decl")
        ident, param_list, ret_typ = tree.children
        super().__init__(
            file, tree, as_tree(ident), as_tree(param_list), opt_map(ret_typ, as_tree)
        )

    @override
    def diag_str(self) -> str:
        return "function declaration"


class FnDefn(FnSpec):
    access: Optional[Access]
    block: BlockExpr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "fn_defn")
        access, ident, param_list, ret_typ, block = tree.children
        super().__init__(
            file, tree, as_tree(ident), as_tree(param_list), opt_map(ret_typ, as_tree)
        )
        self.access = Access.from_tree(file, as_tree(access))
        self.block = BlockExpr(file, as_tree(block))

    @override
    def diag_str(self) -> str:
        return "function definition"


class VarDefn(Defn):
    access: Optional[Access]
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
    access: Optional[Access]
    ident: Ident
    fields: list[StructFieldDefn]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "struct_defn")
        super().__init__(SrcSpan(file, tree.meta))
        access, ident, field_defn_list = map(as_tree, tree.children)
        self.access = Access.from_tree(file, access)
        self.ident = Ident(file, ident)

        assert_eq(field_defn_list.data, "struct_field_defn_list")
        self.fields = [
            StructFieldDefn(file, as_tree(child)) for child in field_defn_list.children
        ]

    @override
    def diag_str(self) -> str:
        return f'struct "{self.ident.name}" definition'


class StructFieldDefn(Ast):
    access: Optional[Access]
    mut: Optional[Mutability]
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


class Import(Defn):
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
    value: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "access")
        super().__init__(SrcSpan(file, tree.meta))
        (child,) = tree.children
        assert child is not None
        self.value = as_token(child)

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Optional[Access]:
        (child,) = tree.children
        if child is not None:
            return Access(file, tree)
        else:
            return None

    @override
    def diag_str(self) -> str:
        return "access specifier"


class Mutability(Ast):
    value: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "mut")
        super().__init__(SrcSpan(file, tree.meta))
        (child,) = tree.children
        self.value = as_token(child)

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Optional[Mutability]:
        (child,) = tree.children
        if child is not None:
            return Mutability(file, tree)
        else:
            return None

    @override
    def diag_str(self) -> str:
        return "mut specifier"


class Mod(Ast):
    defns: list[Defn]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert_eq(tree.data, "mod")
        super().__init__(SrcSpan(file, tree.meta))
        self.defns = [Defn.from_tree(file, as_tree(child)) for child in tree.children]

    @override
    def diag_str(self) -> str:
        return "module"


def opt_ast(obj: Any) -> Optional[Ast]:
    attr = getattr(obj, "ast", None)
    if isinstance(attr, Ast):
        return attr
    return None


def opt_span(obj: Any) -> Optional[SrcSpan]:
    return opt_map(opt_ast(obj), lambda x: x.span)
