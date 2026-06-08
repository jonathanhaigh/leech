from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
import enum
from functools import cached_property
import re
from typing import ChainMap, Generic, Optional, TypeVar, override

from more_itertools import nth
import networkx as nx

from l0 import comptime
from l0 import l0ast as ast
from l0.l0errors import (
    AssignToConstError,
    AssignToVoidError,
    DuplicateFieldInStructExprError,
    DuplicateTypDefnError,
    DuplicateVarDefnError,
    FieldAccessIntoInvalidTypError,
    IfCondNotBoolError,
    IfElsTypMismatchError,
    IfTypNotVoidError,
    IncompatibleBinOpArgTypsError,
    IncompatibleStructFieldTypError,
    IncompatibleTypInArrayExpr,
    IndexIntoInvalidTypError,
    InvalidArgTypError,
    InvalidBinOpArgTypError,
    InvalidIndexTypError,
    InvalidRetTypError,
    InvalidStructFieldError,
    InvalidVoidRetError,
    MissingFieldInStructExprError,
    MissingRetError,
    NotCallableError,
    NotEnoughArgsError,
    TooManyArgsError,
    TypeOfStructExprNotStructError,
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
    I32,
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
    StructTyp,
    Typ,
    VoidTyp,
)

TypT = TypeVar("TypT", bound=Typ, covariant=True, default=Typ)
AstT = TypeVar("AstT", bound=ast.Ast, covariant=True, default=ast.Ast)
FnAstT = TypeVar("FnAstT", bound=ast.FnSpec, covariant=True)


class Env:
    Vars = ChainMap[str, "Value[PtrTyp]"]
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

    def get_var(self, name: str) -> Optional[Value[PtrTyp]]:
        if name in self.vars:
            return self.vars[name]
        return None

    def add_var(self, name: str, var: Value[PtrTyp]) -> None:
        if name in self.vars.maps[0]:
            existing = self.vars[name]
            raise DuplicateVarDefnError(name, var.span, existing.span)
        self.vars[name] = var

    def add_typ(self, name: str, typ: Typ):
        if name in self.typs.maps[0]:
            existing = self.typs[name]
            raise DuplicateTypDefnError(name, typ.span, existing.span)
        self.typs[name] = typ

    def get_typ(self, name: str) -> Optional[Typ]:
        if name in self.typs:
            return self.typs[name]

        m = re.fullmatch("([iu])([1-9][0-9]*)", name)
        if m:
            signage = SIGNED if m[1] == "i" else UNSIGNED
            width = int(m[2])
            typ = IntTyp(width, signage)
            self.typs.maps[-1][name] = typ
            return typ

        return None


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
        for index in self.indeces:
            match typ:
                case PtrTyp():
                    mut = typ.mut
                    typ = typ.pointee_typ
                case ArrayTyp():
                    typ = typ.element_typ
                case StructTyp():
                    assert isinstance(index, ComptimeInt), (
                        "struct indeces must be comptime known"
                    )
                    field = nth(typ.fields.values(), index.value)
                    assert field is not None, (
                        f"invalid field index {index.value} into {typ.name()}"
                    )
                    typ = field.typ
                case _:
                    raise NotImplementedError(typ)

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


class ExprContext(enum.Enum):
    PLACE = 0
    VALUE = 1


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

        block = self.build_expr(fn_ast.block, e, ExprContext.VALUE)
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

    def build_expr(self, expr_ast: ast.Expr, e: Env, ctx: ExprContext) -> Value:
        match expr_ast:
            case ast.BlockExpr():
                return self.build_block_expr(expr_ast, e, ctx)
            case ast.IfExpr():
                return self.build_if_expr(expr_ast, e, ctx)
            case ast.WhileExpr():
                return self.build_while_expr(expr_ast, e, ctx)
            case ast.CallExpr():
                return self.build_call_expr(expr_ast, e, ctx)
            case ast.BinOpExpr():
                return self.build_bin_op_expr(expr_ast, e, ctx)
            case ast.StrLit():
                return self._in_context(ComptimeCStr(expr_ast.value, expr_ast), ctx)
            case ast.IntLit():
                return self._in_context(
                    ComptimeInt(expr_ast.typ, expr_ast.value, expr_ast), ctx
                )
            case ast.BoolLit():
                return self._in_context(ComptimeBool(expr_ast.value, expr_ast), ctx)
            case ast.VarExpr():
                return self.build_var_expr(expr_ast, e, ctx)
            case ast.ArrayExpr():
                return self.build_array_expr(expr_ast, e, ctx)
            case ast.ArrayAccessExpr():
                return self.build_array_access_expr(expr_ast, e, ctx)
            case ast.StructExpr():
                return self.build_struct_expr(expr_ast, e, ctx)
            case ast.StructAccessExpr():
                return self.build_struct_access_expr(expr_ast, e, ctx)
            case _:
                raise NotImplementedError(expr_ast)

    def build_block_expr(
        self, block_ast: ast.BlockExpr, e: Env, ctx: ExprContext
    ) -> Value:
        e = e.new_child()
        for stmt_ast in block_ast.stmts:
            self.build_stmt(stmt_ast, e)

        if block_ast.expr is None:
            return VoidValue(block_ast)
        return self.build_expr(block_ast.expr, e, ctx)

    def build_if_expr(self, if_ast: ast.IfExpr, e: Env, ctx: ExprContext) -> Value:
        cond = self.build_expr(if_ast.condition, e, ExprContext.VALUE)
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
        then = self.build_expr(if_ast.then, e, ctx)
        then_last_bb = self.curr_bb
        if then.typ == NEVER and not then_last_bb.terminated:
            then_last_bb.unreachable(if_ast.then)
        have_then_value = not then_last_bb.terminated
        if have_then_value:
            self.branch(end_bb, if_ast.then)

        if els_bb is not None:
            self.set_position(els_bb)
            assert if_ast.els is not None
            els = self.build_expr(if_ast.els, e, ctx)
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

    def build_while_expr(
        self, while_ast: ast.WhileExpr, e: Env, _ctx: ExprContext
    ) -> Value:
        cond_bb = self.add_bb("while_cond")
        loop_bb = self.add_bb("while_loop")
        end_bb = self.add_bb("while_end")

        self.branch(cond_bb, while_ast)
        cond = self.build_expr(while_ast.condition, e, ExprContext.VALUE)
        if cond.typ != BOOL:
            raise WhileCondNotBoolError(
                while_ast.condition.diag_str(),
                cond.typ.name(),
                while_ast.condition.span,
            )

        self.cbranch(cond, loop_bb, end_bb, while_ast)
        self.set_position(loop_bb)
        block = self.build_expr(while_ast.block, e, ExprContext.VALUE)
        if block.typ not in (NEVER, VOID):
            raise WhileTypNotVoidError(block.typ.name(), while_ast.block.span)

        if not self.curr_bb.terminated:
            self.branch(cond_bb, while_ast.block)

        self.set_position(end_bb)
        return VoidValue(while_ast)

    def build_call_expr(
        self, call_ast: ast.CallExpr, e: Env, ctx: ExprContext
    ) -> Value:
        callee = self.build_expr(call_ast.callee, e, ExprContext.VALUE)
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

        args = [
            self.build_expr(arg_ast, e, ExprContext.VALUE) for arg_ast in call_ast.args
        ]

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

        return self._in_context(self.curr_bb.call(callee, args, call_ast), ctx)

    def build_bin_op_expr(
        self, op_ast: ast.BinOpExpr, e: Env, ctx: ExprContext
    ) -> Value:
        lhs = self.build_expr(op_ast.lhs, e, ExprContext.VALUE)
        if not isinstance(lhs.typ, IntTyp):
            raise InvalidBinOpArgTypError(
                op_ast.op.name,
                op_ast.op.span,
                "left",
                lhs.typ.name(),
                "an integer type",
                op_ast.lhs.span,
            )

        rhs = self.build_expr(op_ast.rhs, e, ExprContext.VALUE)
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
                    res = self.curr_bb.icmp_signed(op, lhs, rhs, op_ast)
                else:
                    res = self.curr_bb.icmp_unsigned(op, lhs, rhs, op_ast)
            case "+":
                res = self.curr_bb.add(lhs, rhs, op_ast)
            case "-":
                res = self.curr_bb.sub(lhs, rhs, op_ast)
            case "*":
                res = self.curr_bb.mul(lhs, rhs, op_ast)
            case "/":
                if lhs.typ.signage == SIGNED:
                    res = self.curr_bb.sdiv(lhs, rhs, op_ast)
                else:
                    res = self.curr_bb.udiv(lhs, rhs, op_ast)
            case _:
                raise NotImplementedError(op)

        return self._in_context(res, ctx)

    def build_var_expr(self, var_ast: ast.VarExpr, e: Env, ctx: ExprContext) -> Value:
        name = var_ast.name
        var = e.get_var(name)
        if var is None:
            raise VarNotFoundError(name, var_ast.span)
        if isinstance(var, FnSpec):
            return var
        if ctx == ExprContext.PLACE:
            return var

        return self.curr_bb.load(var, var_ast)

    def build_array_expr(
        self, arr_expr: ast.ArrayExpr, e: Env, ctx: ExprContext
    ) -> Value:
        elts = [self.build_expr(elt, e, ExprContext.VALUE) for elt in arr_expr.elements]
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

        return self._in_context(arr, ctx)

    def build_array_access_expr(
        self, aa_expr: ast.ArrayAccessExpr, e: Env, ctx: ExprContext
    ) -> Value:
        arr_ptr = self.build_expr(aa_expr.array, e, ExprContext.PLACE)
        assert isinstance(arr_ptr.typ, PtrTyp)
        if not isinstance(arr_ptr.typ.pointee_typ, ArrayTyp):
            raise IndexIntoInvalidTypError(
                arr_ptr.typ.pointee_typ.name(), aa_expr.array.span
            )

        index = self.build_expr(aa_expr.index, e, ExprContext.VALUE)
        if index.typ != USIZE:
            raise InvalidIndexTypError(index.typ.name(), aa_expr.index.span)

        # TODO: bounds check

        zero = ComptimeInt(USIZE, 0, aa_expr)
        elt_ptr = self.curr_bb.gep(arr_ptr, [zero, index], aa_expr)
        if ctx == ExprContext.PLACE:
            return elt_ptr
        else:
            return self.curr_bb.load(elt_ptr, aa_expr)

    def build_struct_expr(
        self, struct_expr: ast.StructExpr, e: Env, ctx: ExprContext
    ) -> Value:
        struct_typ = Typ.from_ast(struct_expr.typ, e)
        if not isinstance(struct_typ, StructTyp):
            raise TypeOfStructExprNotStructError(
                struct_typ.name(), struct_expr.typ.span
            )

        struct = UndefValue(struct_typ, struct_expr)

        field_values = {}
        for field_expr in struct_expr.fields:
            if field_expr.ident.name not in struct_typ.fields:
                raise InvalidStructFieldError(
                    field_expr.ident.name,
                    field_expr.ident.span,
                    struct_typ.name(),
                    struct_typ.span,
                )
            if field_expr.ident.name in field_values:
                raise DuplicateFieldInStructExprError(
                    field_expr.ident.name,
                    field_expr.ident.span,
                    field_values[field_expr.ident.name].span,
                )
            field_values[field_expr.ident.name] = self.build_expr(
                field_expr.value, e, ExprContext.VALUE
            )

        for i, field in enumerate(struct_typ.fields.values()):
            if field.name not in field_values:
                raise MissingFieldInStructExprError(
                    field.name, field.ast.span, struct_typ.name(), struct_expr.span
                )
            field_value = field_values[field.name]
            if field.typ != field_value.typ:
                raise IncompatibleStructFieldTypError(
                    field.name,
                    struct_typ.name(),
                    field_value.typ.name(),
                    field_value.span,
                    field.typ.name(),
                    field.ast.span,
                )
            field_index = ComptimeInt(I32, i, field_value.ast)
            struct = self.curr_bb.insert_value(
                struct, field_value, [field_index], field_value.ast
            )

            # TODO: check field access specifiers

        return self._in_context(struct, ctx)

    def build_struct_access_expr(
        self, sa_expr: ast.StructAccessExpr, e: Env, ctx: ExprContext
    ) -> Value:
        struct_ptr = self.build_expr(sa_expr.struct, e, ExprContext.PLACE)
        assert isinstance(struct_ptr.typ, PtrTyp)
        struct_typ = struct_ptr.typ.pointee_typ
        if not isinstance(struct_typ, StructTyp):
            raise FieldAccessIntoInvalidTypError(struct_typ.name(), sa_expr.struct.span)

        field_name = sa_expr.field.name
        if field_name not in struct_typ.fields:
            raise InvalidStructFieldError(
                field_name, sa_expr.field.span, struct_typ.name(), struct_typ.span
            )

        zero = ComptimeInt(USIZE, 0, sa_expr)
        index = ComptimeInt(I32, struct_typ.fields[field_name].index, sa_expr)
        field_ptr = self.curr_bb.gep(struct_ptr, [zero, index], sa_expr)

        if ctx == ExprContext.PLACE:
            return field_ptr
        else:
            return self.curr_bb.load(field_ptr, sa_expr)

    def build_stmt(self, stmt_ast: ast.Stmt, e: Env) -> None:
        match stmt_ast:
            case ast.ExprStmt():
                self.build_expr(stmt_ast.expr, e, ExprContext.VALUE)
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
            expr = self.build_expr(ret_ast.expr, e, ExprContext.VALUE)
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
        expr = self.build_expr(let_ast.expr, e, ExprContext.VALUE)
        name = let_ast.ident.name
        mut = Mutability.from_ast(let_ast.mut)
        alloca = self.curr_bb.alloca(expr.typ, mut, 1, let_ast)
        e.add_var(name, alloca)
        self.curr_bb.store(expr, alloca, let_ast)

    def build_assignment_stmt(self, ass_ast: ast.AssignmentStmt, e: Env) -> None:
        expr = self.build_expr(ass_ast.expr, e, ExprContext.VALUE)
        place = self.build_expr(ass_ast.place, e, ExprContext.PLACE)

        if place.typ == VOID:
            raise AssignToVoidError(ass_ast.place.span)
        assert isinstance(place.typ, PtrTyp)
        if place.typ.mut == CONST:
            raise AssignToConstError(ass_ast.place.span)

        self.curr_bb.store(expr, place, ass_ast)

    def _value_to_ptr(self, value: Value) -> Value[PtrTyp]:
        alloca = self.curr_bb.alloca(value.typ, CONST, 1, value.ast)
        self.curr_bb.store(value, alloca, value.ast)
        return alloca

    def _in_context(self, value: Value, ctx: ExprContext) -> Value:
        if ctx == ExprContext.PLACE:
            return self._value_to_ptr(value)
        else:
            return value

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


@dataclass
class ModItem:
    name: str
    access: Access
    value: Value[PtrTyp] | Typ


class FnSpec(Generic[FnAstT], Value[PtrTyp, FnAstT]):
    @cached_property
    def params(self) -> tuple[Param, ...]:
        return self.calculate_params()

    @abstractmethod
    def calculate_params(self) -> tuple[Param, ...]:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    def fn_typ(self) -> FnTyp:
        assert isinstance(self.typ.pointee_typ, FnTyp)
        return self.typ.pointee_typ


class BuiltinFn(FnSpec):
    _name: str
    _fn_typ: FnTyp
    _build: Callable[[CfgBuilder, Env], None]
    env: Env

    @override
    def __init__(
        self,
        name: str,
        typ: FnTyp,
        build: Callable[[CfgBuilder, Env], None],
        e: Env,
    ) -> None:
        super().__init__(ast=None)
        self._name = name
        self._fn_typ = typ
        self._build = build
        self.env = e.new_child()

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp(self._fn_typ, CONST)

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        return tuple(
            Param(self, pos, None) for pos in range(len(self._fn_typ.param_typs))
        )

    @override
    def name(self) -> str:
        return self._name

    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        self._build(builder, self.env)
        return builder.cfg


class NonBuiltinFnSpec(Generic[FnAstT], FnSpec[FnAstT]):
    env: Env

    @override
    def __init__(self, ast: FnAstT, e: Env) -> None:
        super().__init__(ast)
        self.env = e.new_child()

    @override
    def calculate_typ(self) -> PtrTyp:
        assert self.ast is not None
        if self.ast.ret_typ is None:
            ret_typ = VOID
        else:
            ret_typ = Typ.from_ast(self.ast.ret_typ, self.env)

        param_typs = (
            Typ.from_ast(param_ast.typ, self.env) for param_ast in self.ast.params
        )
        return PtrTyp(FnTyp(ret_typ, tuple(param_typs)), CONST)

    @override
    def calculate_params(self) -> tuple[Param, ...]:
        assert self.ast is not None
        return tuple(
            Param(self, pos, param_ast) for pos, param_ast in enumerate(self.ast.params)
        )

    @override
    def name(self) -> str:
        assert self.ast is not None
        return self.ast.name.name


class FnDecl(NonBuiltinFnSpec[ast.FnDecl]):
    pass


class Fn(NonBuiltinFnSpec[ast.FnDefn]):
    @cached_property
    def cfg(self) -> Cfg:
        builder = CfgBuilder(self)
        builder.build_fn(opt_unwrap(self.ast), self.env)
        return builder.cfg


class ModVar(Value[PtrTyp, ast.VarDefn]):
    env: Env
    mut: Mutability

    @override
    def __init__(self, ast: ast.VarDefn, e: Env) -> None:
        super().__init__(ast)
        self.env = e.new_child()
        self.mut = Mutability.from_ast(ast.let_stmt.mut)

    @cached_property
    def initializer(self) -> ComptimeValue:
        # TODO: detect recursion
        assert self.ast is not None
        return comptime.Interpreter().eval(self.ast.let_stmt.expr, self.env)

    @override
    def calculate_typ(self) -> PtrTyp:
        return PtrTyp(self.initializer.typ, self.mut)


class Mod(Value[Typ, ast.Mod]):
    _env: Env
    items: list[ModItem]

    @override
    def __init__(self, mod_ast: ast.Mod) -> None:
        super().__init__(mod_ast)
        builtin_env = Env()
        self._env = builtin_env.new_child()
        self.items = []

        builtin_env.add_typ("usize", USIZE)
        builtin_env.add_typ("isize", ISIZE)
        builtin_env.add_typ("bool", BOOL)

        for defn_ast in mod_ast.defns:
            self._build_defn(defn_ast)

    @override
    def calculate_typ(self) -> Typ:
        raise NotImplementedError

    def _build_defn(self, defn_ast: ast.Defn) -> None:
        match defn_ast:
            case ast.VarDefn():
                self._add_item(
                    defn_ast.let_stmt.ident.name,
                    Access.from_ast(defn_ast.access),
                    ModVar(defn_ast, self._env),
                )
            case ast.FnDecl():
                self._add_item(
                    defn_ast.name.name, Access.PUBLIC, FnDecl(defn_ast, self._env)
                )
            case ast.FnDefn():
                self._add_item(
                    defn_ast.name.name,
                    Access.from_ast(defn_ast.access),
                    Fn(defn_ast, self._env),
                )
            case ast.StructDefn():
                self._add_item(
                    defn_ast.ident.name,
                    Access.from_ast(defn_ast.access),
                    StructTyp(defn_ast, self._env),
                )
            case _:
                raise NotImplementedError

    def _add_item(self, name: str, access: Access, value: Value[PtrTyp] | Typ) -> None:
        v = ModItem(name, access, value)
        self.items.append(v)
        if isinstance(value, Value):
            self._env.add_var(name, value)
        else:
            assert isinstance(value, Typ)
            self._env.add_typ(name, value)
