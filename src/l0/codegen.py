from collections import ChainMap
from dataclasses import dataclass
from functools import cache
from typing import Optional
from llvmlite import ir as ll
from networkx import bfs_tree
from l0 import ir
from l0.naming import VarNamer
from l0.typs import VOID, ArrayTyp, BoolTyp, FnTyp, IntTyp, PtrTyp, Typ, VoidTyp


def set_linkage(ll_global: ll.GlobalVariable | ll.Function, access: ir.Access) -> None:
    if access == ir.PRIVATE:
        ll_global.linkage = "private"
    else:
        assert access == ir.PUBLIC


class Compiler:
    mod: ir.Mod
    ll_mod: ll.Module
    _ll_mod_values: Compiler.LLValues
    _tmp_name: VarNamer

    class LLValues:
        compiler: Compiler
        values: ChainMap[ir.Value, ll.Value]

        def __init__(
            self,
            compiler: Compiler,
            values: Optional[ChainMap[ir.Value, ll.Value]] = None,
        ) -> None:
            super().__init__()
            self.compiler = compiler
            if values is not None:
                self.values = values
            else:
                self.values = ChainMap()

        def new_child(self) -> Compiler.LLValues:
            return Compiler.LLValues(self.compiler, self.values.new_child())

        def get(self, value: ir.Value) -> ll.Value:
            if value not in self.values and isinstance(value, ir.ComptimeValue):
                self.values[value] = self.compiler._compile_comptime_value(value)
            return self.values[value]

        def set(self, value: ir.Value, ll_value: ll.Value) -> None:
            self.values[value] = ll_value

    @dataclass
    class _FnBuilderContext:
        ll_builder: ll.IRBuilder
        ll_values: Compiler.LLValues
        ll_bbs: ChainMap[ir.BasicBlock, ll.Block]

    def __init__(self, mod: ir.Mod) -> None:
        self.mod = mod
        self.ll_mod = ll.Module()
        self._ll_mod_values = Compiler.LLValues(self)
        self._tmp_name = VarNamer()

    def compile(self) -> None:
        self.ll_mod.triple = "x86_64-linux-gnu"

        for item in self.mod.items:
            self._ll_mod_values.set(item, self._declare_mod_value(item))

        for item in self.mod.items:
            self._compile_mod_value(item)

    @cache
    def _ll_typ(self, typ: Typ) -> ll.Type:
        match typ:
            case IntTyp():
                return ll.IntType(typ.width)
            case BoolTyp():
                return ll.IntType(1)
            case FnTyp():
                return ll.FunctionType(
                    self._ll_typ(typ.ret_typ),
                    (self._ll_typ(param_typ) for param_typ in typ.param_typs),
                )
            case PtrTyp():
                return ll.PointerType()
            case ArrayTyp():
                return ll.ArrayType(self._ll_typ(typ.element_typ), typ.length)
            case VoidTyp():
                return ll.VoidType()
            case _:
                raise NotImplementedError

    def _declare_mod_value(self, value: ir.Value) -> ll.Value:
        match value:
            case ir.ModVar():
                return self._declare_mod_var(value)
            case ir.FnSpec():
                return self._declare_mod_fn(value)
            case _:
                raise NotImplementedError(value)

    def _compile_mod_value(self, value: ir.Value) -> None:
        match value:
            case ir.ModVar():
                return self._compile_mod_var(value)
            case ir.Fn():
                return self._compile_mod_fn(value)
            case ir.BuiltinFn():
                return self._compile_mod_fn(value)
            case ir.FnDecl():
                return
            case _:
                raise NotImplementedError(value)

    def _declare_mod_var(self, var: ir.ModVar) -> ll.Value:
        ll_val = ll.GlobalVariable(self.ll_mod, self._ll_typ(var.typ), var.name)
        set_linkage(ll_val, var.access)
        return ll_val

    def _compile_mod_var(self, var: ir.ModVar) -> None:
        ll_init = self._compile_comptime_value(var.initializer)
        self._ll_mod_values.get(var).initializer = ll_init  # type: ignore

    def _declare_mod_fn(self, fn: ir.FnSpec) -> ll.Value:
        ll_fn = ll.Function(self.ll_mod, self._ll_typ(fn.typ), fn.name)
        # TODO: linkage for FnDecls?
        set_linkage(ll_fn, fn.access)
        return ll_fn

    def _compile_mod_fn(self, fn: ir.Fn | ir.BuiltinFn) -> None:
        ctx = Compiler._FnBuilderContext(
            ll_builder=ll.IRBuilder(),
            ll_values=self._ll_mod_values.new_child(),
            ll_bbs=ChainMap(),
        )

        ll_fn = self._ll_mod_values.get(fn)
        assert isinstance(ll_fn, ll.Function)

        for param, ll_param in zip(fn.params, ll_fn.args):
            ctx.ll_values.set(param, ll_param)

        bb_tree = bfs_tree(fn.cfg, fn.cfg.entry)
        for bb in bb_tree:
            if bb.name != "exit":
                ctx.ll_bbs[bb] = ll_fn.append_basic_block(bb.name)

        for bb in bb_tree:
            if bb.name != "exit":
                self._compile_bb(bb, ctx)

    def _compile_bb(self, bb: ir.BasicBlock, ctx: Compiler._FnBuilderContext) -> None:
        ctx.ll_builder.position_at_start(ctx.ll_bbs[bb])
        for instr in bb.instrs:
            ctx.ll_values.set(instr, self._compile_instr(instr, ctx))

    def _compile_instr(
        self, instr: ir.Instr, ctx: Compiler._FnBuilderContext
    ) -> ll.Value:
        match instr:
            case ir.AddInstr():
                return ctx.ll_builder.add(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir.SubInstr():
                return ctx.ll_builder.sub(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir.MulInstr():
                return ctx.ll_builder.mul(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir.SdivInstr():
                return ctx.ll_builder.sdiv(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir.UdivInstr():
                return ctx.ll_builder.udiv(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir.IcmpSignedInstr():
                return ctx.ll_builder.icmp_signed(
                    instr.op,
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir.IcmpUnsignedInstr():
                return ctx.ll_builder.icmp_unsigned(
                    instr.op,
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir.LoadInstr():
                return ctx.ll_builder.load(
                    ctx.ll_values.get(instr.src), typ=self._ll_typ(instr.typ)
                )
            case ir.AllocaInstr():
                return ctx.ll_builder.alloca(self._ll_typ(instr.typ))
            case ir.StoreInstr():
                return ctx.ll_builder.store(
                    ctx.ll_values.get(instr.value),
                    ctx.ll_values.get(instr.dest),
                )
            case ir.GepInstr():
                ll_indeces = [ctx.ll_values.get(i) for i in instr.indeces]
                return ctx.ll_builder.gep(ctx.ll_values.get(instr.base), ll_indeces)
            case ir.InsertValueInstr():
                return ctx.ll_builder.insert_value(
                    ctx.ll_values.get(instr.aggregate),
                    ctx.ll_values.get(instr.value),
                    [index.value for index in instr.indeces],
                )
            case ir.CallInstr():
                ll_args = [ctx.ll_values.get(arg) for arg in instr.args]
                return ctx.ll_builder.call(ctx.ll_values.get(instr.callee), ll_args)
            case ir.PhiInstr():
                ll_phi = ctx.ll_builder.phi(self._ll_typ(instr.typ))
                for bb, value in instr.incoming.items():
                    ll_phi.add_incoming(ctx.ll_values.get(value), ctx.ll_bbs[bb])
                return ll_phi
            case ir.BranchInstr():
                return ctx.ll_builder.branch(ctx.ll_bbs[instr.target])
            case ir.CbranchInstr():
                return ctx.ll_builder.cbranch(
                    ctx.ll_values.get(instr.condition),
                    ctx.ll_bbs[instr.true_target],
                    ctx.ll_bbs[instr.false_target],
                )
            case ir.RetInstr():
                if instr.typ == VOID:
                    return ctx.ll_builder.ret_void()
                return ctx.ll_builder.ret(ctx.ll_values.get(instr.value))
            case ir.UnreachableInstr():
                return ctx.ll_builder.unreachable()
            case _:
                raise NotImplementedError(instr)

    def _compile_comptime_value(self, value: ir.ComptimeValue) -> ll.Value:
        match value:
            case ir.UndefValue():
                return ll.Constant(self._ll_typ(value.typ), ll.Undefined)
            case ir.VoidValue():
                raise NotImplementedError
            case ir.ComptimeInt():
                return ll.Constant(self._ll_typ(value.typ), value.value)
            case ir.ComptimeBool():
                return ll.Constant(self._ll_typ(value.typ), 1 if value.value else 0)
            case ir.ComptimeCStr():
                ll_val = ll.GlobalVariable(
                    self.ll_mod,
                    self._ll_typ(value.initializer_typ),
                    self._tmp_name(".strlit"),
                )
                ll_val.initializer = ll.Constant(  # type: ignore
                    self._ll_typ(value.initializer_typ), value.value
                )
                ll_val.linkage = "private"
                return ll_val
            case ir.ComptimeArray():
                elts = (self._compile_comptime_value(elt) for elt in value.elements)
                return ll.Constant(self._ll_typ(value.typ), elts)
            case _:
                raise NotImplementedError(value)
