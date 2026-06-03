from abc import ABC, abstractmethod
import ast as python_ast
from collections.abc import Callable
import re
from typing import Any, Generic, Optional, TypeVar, override

from lark import Token
from lark.tree import Branch, ParseTree, Tree

from l0 import comptime
from l0 import typs
from l0.opt_util import opt_map
from l0.src import SrcFile, SrcSpan
from l0.typs import SIGNED, UNSIGNED

ComptimeValueT = TypeVar("ComptimeValueT", bound="comptime.Value")


def as_token(branch: Branch[Token]) -> Token:
    assert isinstance(branch, Token)
    return branch


def as_tree(branch: Branch[Token]) -> Tree[Token]:
    assert isinstance(branch, Tree)
    return branch


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
        assert tree.data == "ident"
        super().__init__(SrcSpan(file, tree.meta))
        self.name = as_token(tree.children[0])

    @override
    def diag_str(self) -> str:
        return f"identifier {self.name}"


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
            "cmp_expr": BinOpExpr,
            "arith_expr": BinOpExpr,
            "term": BinOpExpr,
        }

    @staticmethod
    def is_expr_tree(tree: ParseTree) -> bool:
        return tree.data in Expr._child_ctors()

    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Expr:
        return Expr._child_ctors()[tree.data](file, tree)


class BlockExpr(Expr):
    stmts: list[Stmt]
    expr: Optional[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "block_expr"
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
        assert tree.data == "if_expr"
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
        assert tree.data == "while_expr"
        super().__init__(SrcSpan(file, tree.meta))
        condition, block = tree.children
        self.condition = Expr.from_tree(file, as_tree(condition))
        self.block = BlockExpr(file, as_tree(block))

    @override
    def diag_str(self) -> str:
        return "while expression"


class LitExpr(Expr, Generic[ComptimeValueT]):
    value: ComptimeValueT

    def __init__(self, span: SrcSpan, value: ComptimeValueT) -> None:
        super().__init__(span)
        self.value = value


class StrLit(LitExpr["comptime.CStr"]):
    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "str_lit"
        (tok,) = tree.children
        value = comptime.CStr(python_ast.literal_eval(as_token(tok)))
        super().__init__(SrcSpan(file, tree.meta), value)

    @override
    def diag_str(self) -> str:
        return "string literal"


class IntLit(LitExpr["comptime.Int"]):
    token: Token

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "int_lit"
        (token,) = tree.children
        m = re.fullmatch("([0-9]+)(?:([iu])([0-9]+))?", as_token(token))
        assert m is not None

        if m[2] is not None:
            signage = SIGNED if m[2] == "i" else UNSIGNED
            width = int(m[3])
        else:
            signage = SIGNED
            width = 32

        value = comptime.Int(typs.IntTyp(width, signage), int(m[1]))
        self.token = as_token(token)
        super().__init__(SrcSpan(file, tree.meta), value)

    @override
    def diag_str(self) -> str:
        return f'int literal "{self.value.value}"'


class BoolLit(LitExpr["comptime.Bool"]):
    _tok: Token

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "bool_lit"
        (tok,) = tree.children
        assert tok == "true" or tok == "false"
        value = comptime.Bool(True if tok == "true" else False)
        super().__init__(SrcSpan(file, tree.meta), value)

    @override
    def diag_str(self) -> str:
        return f'bool literal "{self._tok}"'


class VarExpr(Expr):
    name: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        (child,) = tree.children
        if isinstance(child, Tree):
            assert child.data == "ident"
            assert len(child.children) == 1
            self.name = as_token(child.children[0])
        else:
            self.name = as_token(child)

        self._var = None

    @override
    def diag_str(self) -> str:
        return f'variable "{self.name}"'


class CallExpr(Expr):
    callee: Expr
    args: list[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "call_expr"
        super().__init__(SrcSpan(file, tree.meta))
        callee, arg_list = map(as_tree, tree.children)
        self.callee = Expr.from_tree(file, callee)

        assert arg_list.data == "arg_list"
        self.args = [
            Expr.from_tree(file, as_tree(child)) for child in arg_list.children
        ]

    @override
    def diag_str(self) -> str:
        return "call expression"


class BinOp(Ast):
    name: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        assert len(tree.children) == 1
        self.name = as_token(tree.children[0])

    @override
    def diag_str(self) -> str:
        return f"binary operation ({self.name})"


class BinOpExpr(Expr):
    lhs: Expr
    op: BinOp
    rhs: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        super().__init__(SrcSpan(file, tree.meta))
        lhs, op, rhs = map(as_tree, tree.children)
        self.lhs = Expr.from_tree(file, lhs)
        self.op = BinOp(file, op)
        self.rhs = Expr.from_tree(file, rhs)

    @override
    def diag_str(self) -> str:
        return f"binary operation ({self.op}) expression"


class Stmt(Ast):
    @staticmethod
    def from_tree(file: SrcFile, tree: ParseTree) -> Stmt:
        assert tree.data == "stmt"
        (child,) = tree.children
        assert isinstance(child, Tree)
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
        assert tree.data == "expr_stmt"
        super().__init__(SrcSpan(file, tree.meta))
        (child,) = tree.children
        self.expr = Expr.from_tree(file, as_tree(child))

    @override
    def diag_str(self) -> str:
        return "expression statement"


class RetStmt(Stmt):
    expr: Optional[Expr]

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "ret_stmt"
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
        assert tree.data == "let_stmt"
        super().__init__(SrcSpan(file, tree.meta))
        mut, ident, expr = tree.children
        self.mut = Mutability.from_tree(file, as_tree(mut))
        self.ident = Ident(file, as_tree(ident))
        self.expr = Expr.from_tree(file, as_tree(expr))

    @override
    def diag_str(self) -> str:
        return "let statement"


class AssignmentStmt(Stmt):
    ident: Ident
    expr: Expr

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "assignment_stmt"
        super().__init__(SrcSpan(file, tree.meta))
        ident, expr = tree.children
        self.ident = Ident(file, as_tree(ident))
        self.expr = Expr.from_tree(file, as_tree(expr))

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
        }
        return child_classes[tree.data](file, tree)


class BasicTyp(Typ):
    name: Ident

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "basic_typ"
        super().__init__(SrcSpan(file, tree.meta))
        (ident,) = tree.children
        self.name = Ident(file, as_tree(ident))

    @override
    def diag_str(self) -> str:
        return f'type specifier "{self.name.name}"'


class PtrTyp(Typ):
    pointee_typ: Typ

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "ptr_typ"
        super().__init__(SrcSpan(file, tree.meta))
        (typ,) = tree.children
        self.pointee_typ = Typ.from_tree(file, as_tree(typ))

    @override
    def diag_str(self) -> str:
        return "type specifier"


class Param(Ast):
    name: Ident
    typ: Typ

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "param"
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
        assert tree.data == "defn"
        (child,) = tree.children
        assert isinstance(child, Tree)
        child_classes = {
            "fn_defn": FnDefn,
            "fn_decl": FnDecl,
            "var_defn": VarDefn,
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

        assert param_list.data == "param_list"
        self.params = [Param(file, as_tree(child)) for child in param_list.children]


class FnDecl(FnSpec):
    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "fn_decl"
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
        assert tree.data == "fn_defn"
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
        assert tree.data == "var_defn"
        super().__init__(SrcSpan(file, tree.meta))
        access, let_stmt = tree.children
        self.access = Access.from_tree(file, as_tree(access))
        self.let_stmt = LetStmt(file, as_tree(let_stmt))

    @override
    def diag_str(self) -> str:
        return "variable definition"


class Access(Ast):
    value: str

    def __init__(self, file: SrcFile, tree: ParseTree) -> None:
        assert tree.data == "access"
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
        assert tree.data == "mut"
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
        assert tree.data == "mod"
        super().__init__(SrcSpan(file, tree.meta))
        self.defns = [Defn.from_tree(file, as_tree(child)) for child in tree.children]

    @override
    def diag_str(self) -> str:
        return "module"
