from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
import enum
from functools import cached_property
from typing import ChainMap, Generic, Optional, TypeVar, override

from lark.tree import Meta
import networkx as nx

from l0 import comptime
from l0 import l0ast as ast
from l0.l0errors import (
    AssignToConstError,
    DuplicateVarDefnError,
    IfCondNotBoolError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    InvalidArgTypError,
    InvalidRetTypError,
    InvalidVoidRetError,
    MissingRetError,
    NotCallableError,
    NotEnoughArgsError,
    TooManyArgsError,
    TypNotFoundError,
    UnreachableCodeWarning,
    VarNotFoundError,
    WhileCondNotBoolError,
    WhileTypNotVoidError,
    register_error,
)
from l0.naming import VarNamer
from l0.opt_util import opt_map, opt_or_default, opt_unwrap
from l0.typs import (
    BOOL,
    CONST,
    I32,
    MUT,
    NEVER,
    SIGNED,
    U32,
    U8,
    VOID,
    CallableTyp,
    FnTyp,
    Mutability,
    NeverTyp,
    PtrTyp,
    Signage,
    Typ,
    VoidTyp,
)

TypT = TypeVar("TypT", bound=Typ, covariant=True, default=Typ)
AstT = TypeVar("AstT", bound=ast.Ast, covariant=True, default=ast.Ast)
FnAstT = TypeVar("FnAstT", bound=ast.FnSpec, covariant=True)


@dataclass
class Var:
    value: Value
    mut: Mutability


class Env:
    Vars = ChainMap[str, "Var"]
    Typs = ChainMap[str, Typ]

    vars: Env.Vars
    typs: Env.Typs

    def __init__(
        self, vars: Optional[Env.Vars] = None, typs: Optional[Env.Typs] = None
    ) -> None:
        if vars is None:
            self.vars = Env.Vars()
        else:
            self.vars = vars

        if typs is None:
            self.typs = Env.Typs()
        else:
            self.typs = typs

    def new_child(self) -> Env:
        return Env(self.vars.new_child(), self.typs.new_child())

    def add_var(self, name: str, var: Var) -> None:
        if name in self.vars.maps[0]:
            existing = self.vars[name]
            raise DuplicateVarDefnError(name, var.value.meta, existing.value.meta)
        self.vars[name] = var


def typ_from_ast(typ_ast: ast.Typ, e: Env) -> Typ:
    match typ_ast:
        case ast.BasicTyp():
            name = typ_ast.name.name
            if name not in e.typs:
                raise TypNotFoundError(name, typ_ast.meta)
            return e.typs[name]
        case ast.PtrTyp():
            return PtrTyp(typ_from_ast(typ_ast.pointee_typ, e), CONST)
        case _:
            raise NotImplementedError(ast)


def mut_from_ast(mut_ast: ast.Mutability) -> Mutability:
    if mut_ast.value is None:
        return CONST
    assert mut_ast.value == "mut"
    return MUT


class Value(ABC, Generic[TypT, AstT]):
    ast: Optional[AstT]

    def __init__(self, ast: Optional[AstT]) -> None:
        self.ast = ast

    @cached_property
    def typ(self) -> TypT:
        return self.calculate_typ()

    @property
    def meta(self) -> Optional[Meta]:
        return opt_map(self.ast, lambda x: x.meta)

    @abstractmethod
    def calculate_typ(self) -> TypT:
        pass


class VoidValue(Value[VoidTyp]):
    @override
    def calculate_typ(self) -> VoidTyp:
        return VOID


class Param(Value[Typ, ast.Param]):
    fn: FnSpec
    pos: int

    @override
    def __init__(self, fn: FnSpec, pos: int, ast: Optional[ast.Param]) -> None:
        super().__init__(ast)
        self.fn = fn
        self.pos = pos

    @override
    def calculate_typ(self) -> Typ:
        fn_typ = self.fn.typ
        assert len(fn_typ.param_typs) > self.pos
        return fn_typ.param_typs[self.pos]


class Instr(Value[TypT, AstT], Generic[TypT, AstT]):
    bb: BasicBlock
    ast: AstT

    @override
    def __init__(self, bb: BasicBlock, ast: Optional[AstT]) -> None:
        super().__init__(ast)
        self.bb = bb

    def __str__(self) -> str:
        return self.name

    @property
    def name(self) -> str:
        return type(self).__name__


class ComptimeValueInstr(Instr):
    value: comptime.Value

    @override
    def __init__(
        self, bb: BasicBlock, value: comptime.Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.value = value

    @override
    def calculate_typ(self) -> Typ:
        return self.value.typ


class BinOpInstr(Instr):
    lhs: Value
    rhs: Value

    @override
    def __init__(
        self, bb: BasicBlock, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.lhs = lhs
        self.rhs = rhs

    @override
    def calculate_typ(self) -> Typ:
        assert self.lhs.typ == self.rhs.typ
        return self.lhs.typ


class AddInstr(BinOpInstr):
    pass


class SubInstr(BinOpInstr):
    pass


class MulInstr(BinOpInstr):
    pass


class SdivInstr(BinOpInstr):
    pass


class UdivInstr(BinOpInstr):
    pass


class IcmpInstr(Instr):
    op: str
    lhs: Value
    rhs: Value

    @override
    def __init__(
        self, bb: BasicBlock, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.op = op
        self.lhs = lhs
        self.rhs = rhs

    @override
    def calculate_typ(self) -> Typ:
        return BOOL


class IcmpSignedInstr(IcmpInstr):
    pass


class IcmpUnsignedInstr(IcmpInstr):
    pass


class LoadInstr(Instr):
    src: Value

    @override
    def __init__(self, bb: BasicBlock, src: Value, ast: Optional[ast.VarExpr]) -> None:
        super().__init__(bb, ast)
        self.src = src

    @override
    def calculate_typ(self) -> Typ:
        return self.src.typ


class AllocaInstr(Instr):
    _typ: Typ
    count: int

    @override
    def __init__(
        self, bb: BasicBlock, typ: Typ, count: int, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self._typ = typ
        self.count = count

    @override
    def calculate_typ(self) -> Typ:
        return self._typ


class StoreInstr(Instr[VoidTyp]):
    value: Value
    dest: Value

    @override
    def __init__(
        self, bb: BasicBlock, value: Value, dest: Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.value = value
        self.dest = dest

    @override
    def calculate_typ(self) -> VoidTyp:
        return VOID


class CallInstr(Instr[Typ, ast.CallExpr]):
    callee: FnSpec
    args: list[Value]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        callee: FnSpec,
        args: list[Value],
        ast: Optional[ast.CallExpr],
    ) -> None:
        super().__init__(bb, ast)
        self.callee = callee
        self.args = args

    @override
    def calculate_typ(self) -> Typ:
        return self.callee.typ.ret_typ


class PhiInstr(Instr):
    incoming: dict["BasicBlock", Value]

    @override
    def __init__(self, bb: BasicBlock, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        self.incoming = {}

    def add_incoming(self, value: Value, bb: BasicBlock) -> None:
        assert bb not in self.incoming
        self.incoming[bb] = value

    @override
    def calculate_typ(self) -> Typ:
        values = list(self.incoming.values())
        assert len(values) > 0
        first_typ = values[0].typ
        for value in values:
            assert value.typ == first_typ
        return first_typ


class BranchInstr(Instr[NeverTyp]):
    target: BasicBlock

    @override
    def __init__(
        self, bb: BasicBlock, target: BasicBlock, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.target = target

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class CbranchInstr(Instr[NeverTyp]):
    condition: Value
    true_target: BasicBlock
    false_target: BasicBlock

    @override
    def __init__(
        self,
        bb: BasicBlock,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.condition = condition
        self.true_target = true_target
        self.false_target = false_target

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class RetInstr(Instr[NeverTyp]):
    value: Value

    @override
    def __init__(self, bb: BasicBlock, value: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        self.value = value

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class UnreachableInstr(Instr[NeverTyp]):
    @override
    def __init__(self, bb: BasicBlock, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)

    @override
    def calculate_typ(self) -> NeverTyp:
        return NEVER


class BasicBlock:
    fn: FnSpec
    name: str
    instrs: list[Instr]
    terminated: bool
    warned_unreachable: bool

    def __init__(self, fn: FnSpec, name: str) -> None:
        self.fn = fn
        self.name = name
        self.instrs = []
        self.terminated = False
        self.warned_unreachable = False

    def __str__(self) -> str:
        instrs = "".join(f"\n{instr}" for instr in self.instrs)
        return f"{self.name}:{instrs}"

    def _add_instr[T: Instr](self, instr: T, terminate: bool = False) -> T:
        if self.terminated:
            if not self.warned_unreachable:
                assert instr.ast is not None, (
                    "Shouldn't get unreachable code in builtins"
                )
                register_error(
                    UnreachableCodeWarning(instr.ast.diag_str(), instr.ast.meta)
                )
                self.warned_unreachable = True

            return instr
        self.instrs.append(instr)
        if terminate:
            self.terminated = True
        return instr

    def comptime_value(
        self, value: comptime.Value, ast: Optional[ast.Ast]
    ) -> ComptimeValueInstr:
        return self._add_instr(ComptimeValueInstr(self, value, ast))

    def add(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> AddInstr:
        return self._add_instr(AddInstr(self, lhs, rhs, ast))

    def sub(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> SubInstr:
        return self._add_instr(SubInstr(self, lhs, rhs, ast))

    def mul(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> MulInstr:
        return self._add_instr(MulInstr(self, lhs, rhs, ast))

    def sdiv(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> SdivInstr:
        return self._add_instr(SdivInstr(self, lhs, rhs, ast))

    def udiv(self, lhs: Value, rhs: Value, ast: Optional[ast.Ast]) -> UdivInstr:
        return self._add_instr(UdivInstr(self, lhs, rhs, ast))

    def icmp_signed(
        self, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> IcmpSignedInstr:
        return self._add_instr(IcmpSignedInstr(self, op, lhs, rhs, ast))

    def icmp_unsigned(
        self, op: str, lhs: Value, rhs: Value, ast: Optional[ast.Ast]
    ) -> IcmpUnsignedInstr:
        return self._add_instr(IcmpUnsignedInstr(self, op, lhs, rhs, ast))

    def phi(self, ast: Optional[ast.Ast]) -> PhiInstr:
        return self._add_instr(PhiInstr(self, ast))

    def load(self, src: Value, ast: Optional[ast.VarExpr]) -> LoadInstr:
        return self._add_instr(LoadInstr(self, src, ast))

    def alloca(self, typ: Typ, count: int, ast: Optional[ast.Ast]) -> AllocaInstr:
        return self._add_instr(AllocaInstr(self, typ, count, ast))

    def store(self, value: Value, dest: Value, ast: Optional[ast.Ast]) -> StoreInstr:
        return self._add_instr(StoreInstr(self, value, dest, ast))

    def call(
        self, callee: FnSpec, args: list[Value], ast: Optional[ast.CallExpr]
    ) -> CallInstr:
        return self._add_instr(CallInstr(self, callee, args, ast))

    def branch(self, target: BasicBlock, ast: Optional[ast.Ast]) -> BranchInstr:
        return self._add_instr(BranchInstr(self, target, ast), terminate=True)

    def cbranch(
        self,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast: Optional[ast.Ast],
    ) -> CbranchInstr:
        return self._add_instr(
            CbranchInstr(self, condition, true_target, false_target, ast),
            terminate=True,
        )

    def ret(self, value: Value, ast: Optional[ast.Ast]) -> RetInstr:
        return self._add_instr(RetInstr(self, value, ast), terminate=True)

    def unreachable(self, ast: Optional[ast.Ast]) -> UnreachableInstr:
        return self._add_instr(UnreachableInstr(self, ast), terminate=True)


class Cfg(nx.DiGraph):
    _entry: Optional[BasicBlock]
    _exit: Optional[BasicBlock]

    @property
    def entry(self) -> BasicBlock:
        return opt_unwrap(self._entry)

    @property
    def exit(self) -> BasicBlock:
        return opt_unwrap(self._exit)


class CfgBuilder:
    fn: FnSpec
    cfg: Cfg
    curr_bb: BasicBlock
    _generate_bb_name: VarNamer

    def __init__(self, fn: FnSpec) -> None:
        self.fn = fn
        self.cfg = Cfg()
        self._generate_bb_name = VarNamer()
        self.cfg._entry = self.add_bb("entry")
        self.cfg._exit = self.add_bb("exit")
        self.curr_bb = self.cfg.entry

    def build_fn(self, fn_ast: ast.FnDefn, e: Env) -> None:
        e = e.new_child()

        for param in self.fn.params:
            assert param.ast is not None
            alloca = self.curr_bb.alloca(param.typ, 1, param.ast)
            self.curr_bb.store(param, alloca, param.ast)
            e.add_var(param.ast.name.name, Var(alloca, CONST))

        block = self.build_expr(fn_ast.block, e)
        ret_typ = self.fn.typ.ret_typ
        ret_typ_meta = opt_map(fn_ast.ret_typ, lambda x: x.meta)
        ret_ast = opt_or_default(fn_ast.block.expr, fn_ast.block)

        if block.typ != VOID:
            if block.typ == NEVER:
                if not self.curr_bb.terminated:
                    self.curr_bb.unreachable(ret_ast)
                return
            if block.typ != ret_typ:
                raise InvalidRetTypError(
                    self.fn.name,
                    ret_typ.name(),
                    ret_typ_meta,
                    block.typ.name(),
                    ret_ast.meta,
                )
            self.ret(block, ret_ast)
            return

        if self.curr_bb.terminated:
            return

        if ret_typ != VOID:
            raise MissingRetError(
                self.fn.name, fn_ast.meta, ret_typ.name(), ret_typ_meta
            )

        self.ret(VoidValue(ret_ast), ret_ast)

    def build_expr(self, expr_ast: ast.Expr, e: Env) -> Value:
        match expr_ast:
            case ast.BlockExpr():
                return self.build_block_expr(expr_ast, e)
            case ast.IfExpr():
                return self.build_if_expr(expr_ast, e)
            case ast.WhileExpr():
                return self.build_while_expr(expr_ast, e)
            case ast.CallExpr():
                return self.build_call_expr(expr_ast, e)
            case ast.LitExpr():
                return self.curr_bb.comptime_value(expr_ast.value, expr_ast)
            case ast.VarExpr():
                return self.build_var_expr(expr_ast, e)
            case _:
                raise NotImplementedError(expr_ast)

    def build_block_expr(self, block_ast: ast.BlockExpr, e: Env) -> Value:
        e = e.new_child()
        for stmt_ast in block_ast.stmts:
            self.build_stmt(stmt_ast, e)

        if block_ast.expr is None:
            return VoidValue(block_ast)
        return self.build_expr(block_ast.expr, e)

    def build_if_expr(self, if_ast: ast.IfExpr, e: Env) -> Value:
        cond = self.build_expr(if_ast.condition, e)
        if cond.typ != BOOL:
            raise IfCondNotBoolError(
                if_ast.condition.diag_str(), cond.typ.name(), if_ast.condition.meta
            )
        then_bb = self.add_bb("if")
        end_bb = self.add_bb("endif")
        els_bb = None
        if if_ast.els is not None:
            els_bb = self.add_bb("else")
            self.cbranch(cond, then_bb, els_bb, if_ast)
        else:
            self.cbranch(cond, then_bb, end_bb, if_ast)

        self.set_position(then_bb)
        then = self.build_expr(if_ast.then, e)
        then_last_bb = self.curr_bb
        if then.typ == NEVER and not then_last_bb.terminated:
            then_last_bb.unreachable(if_ast.then)
        have_then_value = not then_last_bb.terminated
        if have_then_value:
            self.branch(end_bb, if_ast.then)

        if els_bb is not None:
            self.set_position(els_bb)
            assert if_ast.els is not None
            els = self.build_expr(if_ast.els, e)
            els_last_bb = self.curr_bb
            if els.typ == NEVER and not els_last_bb.terminated:
                self.curr_bb.unreachable(if_ast.els)
            have_els_value = not els_last_bb.terminated
            if have_els_value:
                self.branch(end_bb, if_ast.els)

            if have_then_value and have_els_value:
                if then.typ != els.typ:
                    raise IfElsTypMismatchError(
                        then.typ.name(),
                        if_ast.then.meta,
                        els.typ.name(),
                        if_ast.els.meta,
                    )
                res = self.curr_bb.phi(if_ast)
                res.add_incoming(els, els_last_bb)
                res.add_incoming(then, then_last_bb)
                return res
            if have_els_value:
                return els
            self.set_position(end_bb)
            if have_then_value:
                return then
            self.curr_bb.unreachable(if_ast)

        if have_then_value:
            if then.typ != VOID:
                raise IfTypNotVoidError(then.typ.name(), if_ast.then.meta)

        self.set_position(end_bb)
        return VoidValue(if_ast)

    def build_while_expr(self, while_ast: ast.WhileExpr, e: Env) -> Value:
        cond_bb = self.add_bb("while_cond")
        loop_bb = self.add_bb("while_loop")
        end_bb = self.add_bb("while_end")

        self.branch(cond_bb, while_ast)
        cond = self.build_expr(while_ast.condition, e)
        if cond.typ != BOOL:
            raise WhileCondNotBoolError(
                while_ast.condition.diag_str(),
                cond.typ.name(),
                while_ast.condition.meta,
            )

        self.cbranch(cond, loop_bb, end_bb, while_ast)
        self.set_position(loop_bb)
        block = self.build_expr(while_ast.block, e)
        if block.typ not in (NEVER, VOID):
            raise WhileTypNotVoidError(block.typ.name(), while_ast.block.meta)

        if not self.curr_bb.terminated:
            self.branch(cond_bb, while_ast.block)

        self.set_position(end_bb)
        return VoidValue(while_ast)

    def build_call_expr(self, call_ast: ast.CallExpr, e: Env) -> Value:
        callee = self.build_expr(call_ast.callee, e)
        callee_diag_str = call_ast.callee.diag_str()
        if not isinstance(callee.typ, CallableTyp):
            raise NotCallableError(
                callee_diag_str, callee.typ.name(), call_ast.callee.meta
            )
        if not isinstance(callee, FnSpec):
            raise NotImplementedError(callee)

        args = [self.build_expr(arg_ast, e) for arg_ast in call_ast.args]

        num_args = len(args)
        param_typs = callee.typ.param_typs
        num_params = len(param_typs)
        if num_args < num_params:
            raise NotEnoughArgsError(
                callee_diag_str, call_ast.meta, num_args, num_params
            )
        if num_args > num_params:
            raise TooManyArgsError(
                callee_diag_str,
                call_ast.meta,
                call_ast.args[-1].meta,
                num_args,
                num_params,
            )
        for i, arg in enumerate(args):
            if arg.typ != param_typs[i]:
                raise InvalidArgTypError(
                    callee_diag_str,
                    call_ast.meta,
                    i + 1,
                    arg.typ.name(),
                    param_typs[i].name(),
                    call_ast.args[i].meta,
                )

        return self.curr_bb.call(callee, args, call_ast)

    def build_var_expr(self, var_ast: ast.VarExpr, e: Env) -> Value:
        name = var_ast.name
        if name not in e.vars:
            raise VarNotFoundError(name, var_ast.meta)
        var = e.vars[name]
        if isinstance(var.value, FnSpec):
            return var.value
        return self.curr_bb.load(var.value, var_ast)

    def build_stmt(self, stmt_ast: ast.Stmt, e: Env) -> None:
        match stmt_ast:
            case ast.ExprStmt():
                self.build_expr(stmt_ast.expr, e)
            case ast.RetStmt():
                self.build_ret_stmt(stmt_ast, e)
            case ast.LetStmt():
                self.build_let_stmt(stmt_ast, e)
            case ast.AssignmentStmt():
                self.build_assignment_stmt(stmt_ast, e)

    def build_ret_stmt(self, ret_ast: ast.RetStmt, e: Env) -> None:
        ret_typ = self.fn.typ.ret_typ
        ret_typ_meta = None
        if self.fn.ast is not None and self.fn.ast.meta is not None:
            ret_typ_meta = self.fn.ast.meta

        if ret_ast.expr is not None:
            expr = self.build_expr(ret_ast.expr, e)
            if expr.typ == NEVER:
                if not self.curr_bb.terminated:
                    self.curr_bb.unreachable(ret_ast)
                return
            if expr.typ != ret_typ:
                raise InvalidRetTypError(
                    self.fn.name,
                    ret_typ.name(),
                    ret_typ_meta,
                    expr.typ.name(),
                    ret_ast.expr.meta,
                )
            self.ret(expr, ret_ast)
            return

        if ret_typ != VOID:
            raise InvalidVoidRetError(
                self.fn.name, ret_typ.name(), ret_typ_meta, ret_ast.meta
            )

        self.ret(VoidValue(ret_ast), ret_ast)

    def build_let_stmt(self, let_ast: ast.LetStmt, e: Env) -> None:
        expr = self.build_expr(let_ast.expr, e)
        name = let_ast.ident.name
        alloca = self.curr_bb.alloca(expr.typ, 1, let_ast)
        e.add_var(name, Var(alloca, mut_from_ast(let_ast.mut)))
        self.curr_bb.store(expr, alloca, let_ast)

    def build_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: Env) -> None:
        expr = self.build_expr(ass_ast.expr, e)
        name = ass_ast.ident.name
        if name not in e.vars:
            raise VarNotFoundError(name, ass_ast.ident.meta)
        var = e.vars[name]
        if var.mut == CONST:
            raise AssignToConstError(name, ass_ast.ident.meta)

        assert not isinstance(var, FnSpec), "Function variables should always be const"

        self.curr_bb.store(expr, var.value, ass_ast)

    def add_bb(self, basename: str) -> BasicBlock:
        bb = BasicBlock(self.fn, self._generate_bb_name(basename))
        self.cfg.add_node(bb)
        return bb

    def add_edge(self, frm: BasicBlock, to: BasicBlock) -> None:
        assert frm in self.cfg
        assert to in self.cfg
        self.cfg.add_edge(frm, to)

    def set_position(self, bb: BasicBlock) -> None:
        assert bb in self.cfg
        self.curr_bb = bb

    def branch(self, target: BasicBlock, ast: ast.Ast) -> None:
        assert target in self.cfg
        self.curr_bb.branch(target, ast)
        self.cfg.add_edge(self.curr_bb, target)
        self.curr_bb = target

    def cbranch(
        self,
        condition: Value,
        true_target: BasicBlock,
        false_target: BasicBlock,
        ast: ast.Ast,
    ) -> None:
        assert true_target in self.cfg
        assert false_target in self.cfg
        self.curr_bb.cbranch(condition, true_target, false_target, ast)
        self.cfg.add_edge(self.curr_bb, true_target)
        self.cfg.add_edge(self.curr_bb, false_target)

    def ret(self, value: Value, ast: Optional[ast.Ast]) -> None:
        self.curr_bb.ret(value, ast)
        self.cfg.add_edge(self.curr_bb, self.cfg.exit)

    def at_exit(self) -> bool:
        return self.curr_bb == self.cfg.exit


class Access(enum.Enum):
    PRIVATE = 0
    PUBLIC = 1

    @staticmethod
    def from_ast(ast: ast.Access) -> Access:
        if ast.value is None:
            return PRIVATE
        assert ast.value == "pub"
        return PUBLIC


PRIVATE = Access.PRIVATE
PUBLIC = Access.PUBLIC


class ModItem(Generic[TypT, AstT], Value[TypT, AstT]):
    mod: Mod
    name: str
    access: Access
    mut: Mutability
    env: Env

    @override
    def __init__(
        self, mod: Mod, name: str, access: Access, mut: Mutability, ast: AstT, e: Env
    ) -> None:
        super().__init__(ast)
        self.mod = mod
        self.name = name
        self.access = access
        self.mut = mut
        self.env = e


class FnSpec(Generic[FnAstT], ModItem[FnTyp, FnAstT]):
    @cached_property
    def params(self) -> tuple[Param, ...]:
        return self.calculate_params()

    @abstractmethod
    def calculate_params(self) -> tuple[Param, ...]:
        pass


class BuiltinFn(FnSpec):
    _typ: FnTyp
    _build: Callable[[CfgBuilder, Env], None]

    @override
    def __init__(
        self,
        mod: Mod,
        access: Access,
        name: str,
        typ: FnTyp,
        build: Callable[[CfgBuilder, Env], None],
        e: Env,
    ) -> None:
        super().__init__(mod, name, access, CONST, None, e.new_child())
        self._typ = typ
        self._build = build

    @override
    def calculate_typ(self) -> FnTyp:
        return self._typ

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        return tuple(Param(self, pos, None) for pos in range(len(self.typ.param_typs)))

    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        self._build(builder, self.env)
        return builder.cfg


class NonBuiltinFnSpec(Generic[FnAstT], FnSpec[FnAstT]):
    @override
    def __init__(self, mod: Mod, access: Access, ast: FnAstT, e: Env) -> None:
        super().__init__(mod, ast.name.name, access, CONST, ast, e.new_child())

    @override
    def calculate_typ(self) -> FnTyp:
        assert self.ast is not None
        if self.ast.ret_typ is None:
            ret_typ = VOID
        else:
            ret_typ = typ_from_ast(self.ast.ret_typ, self.env)

        param_typs = [
            typ_from_ast(param_ast.typ, self.env)
            for param_ast in self.ast.param_list.params
        ]
        return FnTyp(ret_typ, tuple(param_typs))

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        assert self.ast is not None
        return tuple(
            Param(self, pos, param_ast)
            for pos, param_ast in enumerate(self.ast.param_list.params)
        )


class FnDecl(NonBuiltinFnSpec[ast.FnDecl]):
    pass


class Fn(NonBuiltinFnSpec[ast.FnDefn]):
    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        builder.build_fn(opt_unwrap(self.ast), self.env)
        nx.write_graphml(builder.cfg, f"{self.name}.graphml")
        return builder.cfg


class ModVar(ModItem[Typ, ast.VarDefn]):
    env: Env

    @override
    def __init__(self, mod: Mod, ast: ast.VarDefn, e: Env) -> None:
        name = ast.let_stmt.ident.name
        access = Access.from_ast(ast.access)
        mut = mut_from_ast(ast.let_stmt.mut)
        super().__init__(mod, name, access, mut, ast, e)
        self.env = e.new_child()

    @cached_property
    def initializer(self) -> comptime.Value:
        # TODO: detect recursion
        assert self.ast is not None
        return comptime.Interpreter().eval(self.ast.let_stmt.expr, self.env)

    @override
    def calculate_typ(self) -> Typ:
        assert self.ast is not None
        return self.initializer.typ


class Mod(Value[Typ, ast.Mod]):
    _generate_name: VarNamer
    _env: Env
    items: list[ModItem]

    @override
    def __init__(self, mod_ast: ast.Mod) -> None:
        super().__init__(mod_ast)
        self._generate_name = VarNamer()
        self._env = Env()
        self.items = []

        self._env.typs["i32"] = I32
        self._env.typs["u8"] = U8

        self._define_arith_fn("+", SIGNED, BasicBlock.add)
        self._define_arith_fn("-", SIGNED, BasicBlock.sub)
        self._define_arith_fn("*", SIGNED, BasicBlock.mul)
        self._define_arith_fn("/", SIGNED, BasicBlock.sdiv)

        self._define_cmp_fn("<", SIGNED)
        self._define_cmp_fn("<=", SIGNED)
        self._define_cmp_fn("==", SIGNED)
        self._define_cmp_fn("!=", SIGNED)
        self._define_cmp_fn(">=", SIGNED)
        self._define_cmp_fn(">", SIGNED)

        for defn_ast in mod_ast.defns:
            self._build_defn(defn_ast)

    def _define_arith_fn(
        self,
        name: str,
        signage: Signage,
        bb_method: Callable[[BasicBlock, Value, Value, Optional[ast.Ast]], Instr],
    ) -> None:
        def build(builder: CfgBuilder, _e: Env):
            lhs, rhs = builder.fn.params
            res = bb_method(builder.curr_bb, lhs, rhs, None)
            builder.ret(res, None)

        param_typ = I32 if signage == SIGNED else U32
        fn_typ = FnTyp(param_typ, (param_typ, param_typ))
        self._add_item(BuiltinFn(self, PRIVATE, name, fn_typ, build, self._env))

    def _define_cmp_fn(self, name: str, signage: Signage) -> None:
        def build(builder: CfgBuilder, _e: Env):
            lhs, rhs = builder.fn.params
            if signage == SIGNED:
                res = builder.curr_bb.icmp_signed(name, lhs, rhs, None)
            else:
                res = builder.curr_bb.icmp_unsigned(name, lhs, rhs, None)
            builder.ret(res, None)

        param_typ = I32 if signage == SIGNED else U32
        fn_typ = FnTyp(BOOL, (param_typ, param_typ))
        self._add_item(BuiltinFn(self, PRIVATE, name, fn_typ, build, self._env))

    @override
    def calculate_typ(self) -> Typ:
        raise NotImplementedError

    def _build_defn(self, defn_ast: ast.Defn) -> None:
        match defn_ast:
            case ast.VarDefn():
                self._add_item(ModVar(self, defn_ast, self._env))
            case ast.FnDecl():
                self._add_item(FnDecl(self, Access.PUBLIC, defn_ast, self._env))
            case ast.FnDefn():
                self._add_item(
                    Fn(self, Access.from_ast(defn_ast.access), defn_ast, self._env)
                )
            case _:
                raise NotImplementedError

    def _add_item(self, item: ModItem) -> None:
        self.items.append(item)
        self._env.add_var(item.name, Var(item, item.mut))
