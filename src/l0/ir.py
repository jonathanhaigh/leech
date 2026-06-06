from abc import ABC, abstractmethod
from collections.abc import Callable
import enum
from functools import cached_property
import re
from typing import ChainMap, Generic, Optional, TypeVar, override

import networkx as nx

from l0 import comptime
from l0 import l0ast as ast
from l0.l0errors import (
    AssignToConstError,
    DuplicateTypDefnError,
    DuplicateVarDefnError,
    IfCondNotBoolError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    IncompatibleBinOpArgTypsError,
    IncompatibleTypInArrayExpr,
    IndexIntoInvalidTypError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    InvalidIndexTypError,
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
from l0.src import SrcSpan
from l0.typs import (
    BOOL,
    CONST,
    CSTR,
    ISIZE,
    MUT,
    NEVER,
    SIGNED,
    U8,
    UNSIGNED,
    USIZE,
    VOID,
    ArrayTyp,
    BoolTyp,
    CallableTyp,
    FnTyp,
    IntTyp,
    Mutability,
    NeverTyp,
    PtrTyp,
    Typ,
    VoidTyp,
)

TypT = TypeVar("TypT", bound=Typ, covariant=True, default=Typ)
AstT = TypeVar("AstT", bound=ast.Ast, covariant=True, default=ast.Ast)
FnAstT = TypeVar("FnAstT", bound=ast.FnSpec, covariant=True)


class TypDefn:
    typ: Typ
    ast: Optional[ast.Ast]

    def __init__(self, typ, ast: Optional[ast.Ast]) -> None:
        self.typ = typ
        self.ast = ast

    @property
    def span(self) -> Optional[SrcSpan]:
        return opt_map(self.ast, lambda x: x.span)


class Env:
    Vars = ChainMap[str, "Value[PtrTyp]"]
    Typs = ChainMap[str, TypDefn]

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

    def get_var(self, name: str) -> Optional[Value[PtrTyp]]:
        if name in self.vars:
            return self.vars[name]
        return None

    def add_var(self, name: str, var: Value[PtrTyp]) -> None:
        if name in self.vars.maps[0]:
            existing = self.vars[name]
            raise DuplicateVarDefnError(name, var.span, existing.span)
        self.vars[name] = var

    def add_typ_defn(self, name: str, typ_defn: TypDefn):
        if name in self.typs.maps[0]:
            existing = self.typs[name]
            raise DuplicateTypDefnError(name, typ_defn.span, existing.span)
        self.typs[name] = typ_defn

    def get_typ_defn(self, name: str) -> Optional[TypDefn]:
        if name in self.typs:
            return self.typs[name]

        m = re.fullmatch("([iu])([1-9][0-9]*)", name)
        if m:
            signage = SIGNED if m[1] == "i" else UNSIGNED
            width = int(m[2])
            typ_defn = TypDefn(IntTyp(width, signage), None)
            self.typs.maps[-1][name] = typ_defn
            return typ_defn

        return None

    def get_typ(self, name) -> Optional[Typ]:
        return opt_map(self.get_typ_defn(name), lambda x: x.typ)


def typ_from_ast(typ_ast: ast.Typ, e: Env) -> Typ:
    match typ_ast:
        case ast.BasicTyp():
            name = typ_ast.name.name
            typ = e.get_typ(name)
            if typ is None:
                raise TypNotFoundError(name, typ_ast.span)
            return typ
        case ast.PtrTyp():
            return PtrTyp(typ_from_ast(typ_ast.pointee_typ, e), CONST)
        case ast.ArrayTyp():
            return ArrayTyp(typ_from_ast(typ_ast.element_typ, e), typ_ast.length.value)
        case _:
            raise NotImplementedError(ast)


def mut_from_ast(mut_ast: Optional[ast.Mutability]) -> Mutability:
    if mut_ast is None:
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
    def span(self) -> Optional[SrcSpan]:
        return opt_map(self.ast, lambda x: x.span)

    @abstractmethod
    def calculate_typ(self) -> TypT:
        pass


class ComptimeValue(Generic[TypT, AstT], Value[TypT, AstT]):
    _typ: TypT

    @override
    def __init__(self, typ: TypT, ast: Optional[AstT]) -> None:
        super().__init__(ast)
        self._typ = typ

    @override
    def calculate_typ(self) -> TypT:
        return self._typ


class ComptimeInt(ComptimeValue[IntTyp]):
    value: int

    def __init__(self, typ: IntTyp, value: int, ast: Optional[ast.Ast]) -> None:
        super().__init__(typ, ast)
        if value.bit_length() > typ.width:
            raise NotImplementedError
        self.value = value


class ComptimeBool(ComptimeValue[BoolTyp]):
    value: bool

    def __init__(self, value: bool, ast: Optional[ast.Ast]) -> None:
        super().__init__(BOOL, ast)
        self.value = value


class ComptimeCStr(ComptimeValue[PtrTyp]):
    value: bytearray
    initializer_type: ArrayTyp

    def __init__(self, value: str, ast: Optional[ast.Ast]) -> None:
        super().__init__(CSTR, ast)
        self.value = bytearray(value.encode() + b"\0")
        self.initializer_typ = ArrayTyp(U8, len(self.value))


class ComptimeArray(ComptimeValue[ArrayTyp]):
    elements: list[ComptimeValue]

    @override
    def __init__(
        self, typ: ArrayTyp, elements: list[ComptimeValue], ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(typ, ast)
        self.elements = elements


class VoidValue(ComptimeValue[VoidTyp]):
    @override
    def __init__(self, ast: Optional[ast.Ast]):
        super().__init__(VOID, ast)


class UndefValue(ComptimeValue):
    @override
    def __init__(self, typ: Typ, ast: Optional[ast.Ast]) -> None:
        super().__init__(typ, ast)


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
        assert len(self.fn.fn_typ.param_typs) > self.pos
        return self.fn.fn_typ.param_typs[self.pos]


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
    def __init__(self, bb: BasicBlock, src: Value, ast: Optional[ast.Ast]) -> None:
        super().__init__(bb, ast)
        assert isinstance(src.typ, PtrTyp)
        self.src = src

    @override
    def calculate_typ(self) -> Typ:
        assert isinstance(self.src.typ, PtrTyp)
        return self.src.typ.pointee_typ


class AllocaInstr(Instr[PtrTyp]):
    allocated_typ: Typ
    mut: Mutability
    count: int

    @override
    def __init__(
        self,
        bb: BasicBlock,
        typ: Typ,
        mut: Mutability,
        count: int,
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.allocated_typ = typ
        self.mut = mut
        self.count = count

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp(self.allocated_typ, self.mut)


class StoreInstr(Instr[VoidTyp]):
    value: Value
    dest: Value

    @override
    def __init__(
        self, bb: BasicBlock, value: Value, dest: Value, ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        assert isinstance(dest.typ, PtrTyp)
        assert value.typ == dest.typ.pointee_typ
        self.value = value
        self.dest = dest

    @override
    def calculate_typ(self) -> VoidTyp:
        return VOID


class GepInstr(Instr):
    base: Value
    indeces: list[Value]

    @override
    def __init__(
        self, bb: BasicBlock, base: Value, indeces: list[Value], ast: Optional[ast.Ast]
    ) -> None:
        super().__init__(bb, ast)
        self.base = base
        self.indeces = indeces

    @override
    def calculate_typ(self) -> Typ:
        mut = MUT
        typ = self.base.typ
        for _ in self.indeces:
            if isinstance(typ, PtrTyp):
                mut = typ.mut
                typ = typ.pointee_typ
            elif isinstance(typ, ArrayTyp):
                typ = typ.element_typ
        return PtrTyp(typ, mut)


class InsertValueInstr(Instr):
    aggregate: Value
    value: Value
    indeces: list[ComptimeInt]

    @override
    def __init__(
        self,
        bb: BasicBlock,
        aggregate: Value,
        value: Value,
        indeces: list[ComptimeInt],
        ast: Optional[ast.Ast],
    ) -> None:
        super().__init__(bb, ast)
        self.aggregate = aggregate
        self.value = value
        self.indeces = indeces

    @override
    def calculate_typ(self) -> Typ:
        return self.aggregate.typ


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
        return self.callee.fn_typ.ret_typ


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
                    UnreachableCodeWarning(instr.ast.diag_str(), instr.ast.span)
                )
                self.warned_unreachable = True

            return instr
        self.instrs.append(instr)
        if terminate:
            self.terminated = True
        return instr

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

    def load(self, src: Value, ast: Optional[ast.Ast]) -> LoadInstr:
        return self._add_instr(LoadInstr(self, src, ast))

    def alloca(
        self, typ: Typ, mut: Mutability, count: int, ast: Optional[ast.Ast]
    ) -> AllocaInstr:
        return self._add_instr(AllocaInstr(self, typ, mut, count, ast))

    def store(self, value: Value, dest: Value, ast: Optional[ast.Ast]) -> StoreInstr:
        return self._add_instr(StoreInstr(self, value, dest, ast))

    def gep(
        self, base: Value, indeces: list[Value], ast: Optional[ast.Ast]
    ) -> GepInstr:
        return self._add_instr(GepInstr(self, base, indeces, ast))

    def insert_value(
        self,
        aggregate: Value,
        value: Value,
        indeces: list[ComptimeInt],
        ast: Optional[ast.Ast],
    ) -> InsertValueInstr:
        return self._add_instr(InsertValueInstr(self, aggregate, value, indeces, ast))

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
            alloca = self.curr_bb.alloca(param.typ, CONST, 1, param.ast)
            self.curr_bb.store(param, alloca, param.ast)
            e.add_var(param.ast.name.name, alloca)

        block = self.build_expr(fn_ast.block, e)
        ret_typ = self.fn.fn_typ.ret_typ
        ret_typ_span = opt_map(fn_ast.ret_typ, lambda x: x.span)
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
                    ret_typ_span,
                    block.typ.name(),
                    ret_ast.span,
                )
            self.ret(block, ret_ast)
            return

        if self.curr_bb.terminated:
            return

        if ret_typ != VOID:
            raise MissingRetError(
                self.fn.name, fn_ast.span, ret_typ.name(), ret_typ_span
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
            case ast.BinOpExpr():
                return self.build_bin_op_expr(expr_ast, e)
            case ast.StrLit():
                return ComptimeCStr(expr_ast.value, expr_ast)
            case ast.IntLit():
                return ComptimeInt(expr_ast.typ, expr_ast.value, expr_ast)
            case ast.BoolLit():
                return ComptimeBool(expr_ast.value, expr_ast)
            case ast.VarExpr():
                return self.build_var_expr(expr_ast, e)
            case ast.ArrayExpr():
                return self.build_array_expr(expr_ast, e)
            case ast.ArrayAccessExpr():
                return self.build_array_access_expr(expr_ast, e)
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
                if_ast.condition.diag_str(), cond.typ.name(), if_ast.condition.span
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
                        if_ast.then.span,
                        els.typ.name(),
                        if_ast.els.span,
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
                raise IfTypNotVoidError(then.typ.name(), if_ast.then.span)

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
                while_ast.condition.span,
            )

        self.cbranch(cond, loop_bb, end_bb, while_ast)
        self.set_position(loop_bb)
        block = self.build_expr(while_ast.block, e)
        if block.typ not in (NEVER, VOID):
            raise WhileTypNotVoidError(block.typ.name(), while_ast.block.span)

        if not self.curr_bb.terminated:
            self.branch(cond_bb, while_ast.block)

        self.set_position(end_bb)
        return VoidValue(while_ast)

    def build_call_expr(self, call_ast: ast.CallExpr, e: Env) -> Value:
        callee = self.build_expr(call_ast.callee, e)
        callee_diag_str = call_ast.callee.diag_str()
        if not (
            isinstance(callee.typ, PtrTyp)
            and isinstance(callee.typ.pointee_typ, CallableTyp)
        ):
            raise NotCallableError(
                callee_diag_str, callee.typ.name(), call_ast.callee.span
            )
        if not isinstance(callee, FnSpec):
            raise NotImplementedError(callee)

        args = [self.build_expr(arg_ast, e) for arg_ast in call_ast.args]

        num_args = len(args)
        param_typs = callee.fn_typ.param_typs
        num_params = len(param_typs)
        if num_args < num_params:
            raise NotEnoughArgsError(
                callee_diag_str, call_ast.span, num_args, num_params
            )
        if num_args > num_params:
            raise TooManyArgsError(
                callee_diag_str,
                call_ast.span,
                call_ast.args[-1].span,
                num_args,
                num_params,
            )
        for i, arg in enumerate(args):
            if arg.typ != param_typs[i]:
                raise InvalidArgTypError(
                    callee_diag_str,
                    call_ast.span,
                    i + 1,
                    arg.typ.name(),
                    param_typs[i].name(),
                    call_ast.args[i].span,
                )

        return self.curr_bb.call(callee, args, call_ast)

    def build_bin_op_expr(self, op_ast: ast.BinOpExpr, e: Env) -> Value:
        lhs = self.build_expr(op_ast.lhs, e)
        if not isinstance(lhs.typ, IntTyp):
            raise InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "left",
                lhs.typ.name(),
                "an integer type",
                op_ast.lhs.span,
            )

        rhs = self.build_expr(op_ast.rhs, e)
        if lhs.typ != rhs.typ:
            raise IncompatibleBinOpArgTypsError(
                op_ast.op.name,
                op_ast.op.span,
                lhs.typ.name(),
                op_ast.lhs.span,
                rhs.typ.name(),
                op_ast.rhs.span,
            )

        op = op_ast.op.name
        match op:
            case "<" | "<=" | "==" | "!=" | ">=" | ">":
                if lhs.typ.signage == SIGNED:
                    return self.curr_bb.icmp_signed(op, lhs, rhs, op_ast)
                else:
                    return self.curr_bb.icmp_unsigned(op, lhs, rhs, op_ast)
            case "+":
                return self.curr_bb.add(lhs, rhs, op_ast)
            case "-":
                return self.curr_bb.sub(lhs, rhs, op_ast)
            case "*":
                return self.curr_bb.mul(lhs, rhs, op_ast)
            case "/":
                if lhs.typ.signage == SIGNED:
                    return self.curr_bb.sdiv(lhs, rhs, op_ast)
                else:
                    return self.curr_bb.udiv(lhs, rhs, op_ast)
            case _:
                raise NotImplementedError(op)

    def build_var_expr(self, var_ast: ast.VarExpr, e: Env) -> Value:
        name = var_ast.name
        var = e.get_var(name)
        if var is None:
            raise VarNotFoundError(name, var_ast.span)
        if isinstance(var, FnSpec):
            return var
        return self.curr_bb.load(var, var_ast)

    def build_array_expr(self, arr_expr: ast.ArrayExpr, e: Env) -> Value:
        elts = [self.build_expr(elt, e) for elt in arr_expr.elements]
        if not elts:
            raise NotImplementedError("empty array expression")

        elt_typ = elts[0].typ
        arr_typ = ArrayTyp(elt_typ, len(elts))
        arr = UndefValue(arr_typ, arr_expr)

        for i, elt in enumerate(elts):
            elt_ast = arr_expr.elements[i]
            if elt.typ != elt_typ:
                raise IncompatibleTypInArrayExpr(
                    elt.typ.name(), i, elt_ast.span, arr_typ.name()
                )
            elt_index = ComptimeInt(USIZE, i, elt_ast)
            arr = self.curr_bb.insert_value(arr, elt, [elt_index], elt_ast)

        return arr

    def build_array_access_expr(self, aa_expr: ast.ArrayAccessExpr, e: Env) -> Value:
        arr = self.build_expr(aa_expr.array, e)
        if isinstance(arr.typ, PtrTyp) and isinstance(arr.typ.pointee_typ, ArrayTyp):
            arr_ptr = arr
        elif isinstance(arr.typ, ArrayTyp):
            arr_ptr = self.curr_bb.alloca(arr.typ, CONST, 1, aa_expr)
            self.curr_bb.store(arr, arr_ptr, aa_expr)
        else:
            raise IndexIntoInvalidTypError(arr.typ.name(), aa_expr.array.span)

        index = self.build_expr(aa_expr.index, e)
        if index.typ != USIZE:
            raise InvalidIndexTypError(index.typ.name(), aa_expr.index.span)

        # TODO: bounds check

        zero = ComptimeInt(USIZE, 0, aa_expr)
        elt_ptr = self.curr_bb.gep(arr_ptr, [zero, index], aa_expr)
        return self.curr_bb.load(elt_ptr, aa_expr)

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
        ret_typ = self.fn.fn_typ.ret_typ
        ret_typ_span = None
        if self.fn.ast is not None and self.fn.ast.span is not None:
            ret_typ_span = self.fn.ast.span

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
                    ret_typ_span,
                    expr.typ.name(),
                    ret_ast.expr.span,
                )
            self.ret(expr, ret_ast)
            return

        if ret_typ != VOID:
            raise InvalidVoidRetError(
                self.fn.name, ret_typ.name(), ret_typ_span, ret_ast.span
            )

        self.ret(VoidValue(ret_ast), ret_ast)

    def build_let_stmt(self, let_ast: ast.LetStmt, e: Env) -> None:
        expr = self.build_expr(let_ast.expr, e)
        name = let_ast.ident.name
        mut = mut_from_ast(let_ast.mut)
        alloca = self.curr_bb.alloca(expr.typ, mut, 1, let_ast)
        e.add_var(name, alloca)
        self.curr_bb.store(expr, alloca, let_ast)

    def build_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: Env) -> None:
        expr = self.build_expr(ass_ast.expr, e)
        name = ass_ast.ident.name
        var = e.get_var(name)
        if var is None:
            raise VarNotFoundError(name, ass_ast.ident.span)
        if var.typ.mut == CONST:
            raise AssignToConstError(name, ass_ast.ident.span)

        assert not isinstance(var, FnSpec), "Function variables should always be const"

        self.curr_bb.store(expr, var, ass_ast)

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
    def from_ast(ast: Optional[ast.Access]) -> Access:
        if ast is None:
            return PRIVATE
        assert ast.value == "pub"
        return PUBLIC


PRIVATE = Access.PRIVATE
PUBLIC = Access.PUBLIC


class ModItem(Generic[AstT], Value[PtrTyp, AstT]):
    mod: Mod
    name: str
    access: Access
    env: Env

    @override
    def __init__(self, mod: Mod, name: str, access: Access, ast: AstT, e: Env) -> None:
        super().__init__(ast)
        self.mod = mod
        self.name = name
        self.access = access
        self.env = e


class FnSpec(Generic[FnAstT], ModItem[FnAstT]):
    @cached_property
    def params(self) -> tuple[Param, ...]:
        return self.calculate_params()

    @abstractmethod
    def calculate_params(self) -> tuple[Param, ...]:
        pass

    @property
    def fn_typ(self) -> FnTyp:
        assert isinstance(self.typ.pointee_typ, FnTyp)
        return self.typ.pointee_typ


class BuiltinFn(FnSpec):
    _fn_typ: FnTyp
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
        super().__init__(mod, name, access, None, e.new_child())
        self._fn_typ = typ
        self._build = build

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp(self._fn_typ, CONST)

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        return tuple(
            Param(self, pos, None) for pos in range(len(self._fn_typ.param_typs))
        )

    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        self._build(builder, self.env)
        return builder.cfg


class NonBuiltinFnSpec(Generic[FnAstT], FnSpec[FnAstT]):
    @override
    def __init__(self, mod: Mod, access: Access, ast: FnAstT, e: Env) -> None:
        super().__init__(mod, ast.name.name, access, ast, e.new_child())

    @override
    def calculate_typ(self) -> PtrTyp:
        assert self.ast is not None
        if self.ast.ret_typ is None:
            ret_typ = VOID
        else:
            ret_typ = typ_from_ast(self.ast.ret_typ, self.env)

        param_typs = [
            typ_from_ast(param_ast.typ, self.env) for param_ast in self.ast.params
        ]
        return PtrTyp(FnTyp(ret_typ, tuple(param_typs)), CONST)

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        assert self.ast is not None
        return tuple(
            Param(self, pos, param_ast) for pos, param_ast in enumerate(self.ast.params)
        )


class FnDecl(NonBuiltinFnSpec[ast.FnDecl]):
    pass


class Fn(NonBuiltinFnSpec[ast.FnDefn]):
    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        builder.build_fn(opt_unwrap(self.ast), self.env)
        return builder.cfg


class ModVar(ModItem[ast.VarDefn]):
    env: Env
    mut: Mutability

    @override
    def __init__(self, mod: Mod, ast: ast.VarDefn, e: Env) -> None:
        name = ast.let_stmt.ident.name
        access = Access.from_ast(ast.access)
        super().__init__(mod, name, access, ast, e)
        self.env = e.new_child()
        self.mut = mut_from_ast(ast.let_stmt.mut)

    @cached_property
    def initializer(self) -> ComptimeValue:
        # TODO: detect recursion
        assert self.ast is not None
        return comptime.Interpreter().eval(self.ast.let_stmt.expr, self.env)

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp(self.initializer.typ, self.mut)


class Mod(Value[Typ, ast.Mod]):
    _generate_name: VarNamer
    _env: Env
    items: list[ModItem]

    @override
    def __init__(self, mod_ast: ast.Mod) -> None:
        super().__init__(mod_ast)
        self._generate_name = VarNamer()
        builtin_env = Env()
        self._env = builtin_env.new_child()
        self.items = []

        builtin_env.add_typ_defn("usize", TypDefn(USIZE, None))
        builtin_env.add_typ_defn("isize", TypDefn(ISIZE, None))
        builtin_env.add_typ_defn("bool", TypDefn(BOOL, None))

        for defn_ast in mod_ast.defns:
            self._build_defn(defn_ast)

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
        self._env.add_var(item.name, item)
