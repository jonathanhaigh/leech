from collections import ChainMap
from dataclasses import dataclass
from typing import Optional
from llvmlite import ir as ll
from networkx import bfs_tree
from l0 import ir
from l0.asserts import assert_eq, checked_cast
from l0.naming import VarNamer
from l0.typs import (
    VOID,
    ArrayTyp,
    BoolTyp,
    FnTyp,
    IntTyp,
    PtrTyp,
    StructTyp,
    Typ,
    VoidTyp,
)


def set_linkage(ll_global: ll.GlobalVariable | ll.Function, access: ir.Access) -> None:
    if access == ir.PRIVATE:
        ll_global.linkage = "private"
    else:
        assert_eq(access, ir.PUBLIC)


class Compiler:
    mod: ir.Mod
    ll_mod: ll.Module
    _ll_mod_items: Compiler.LLItems
    _tmp_name: VarNamer

    class LLItems:
        compiler: Compiler
        items: ChainMap[ir.Value | Typ, ll.Value | ll.Type]

        def __init__(
            self,
            compiler: Compiler,
            values: Optional[ChainMap[ir.Value | Typ, ll.Value | ll.Type]] = None,
        ) -> None:
            super().__init__()
            self.compiler = compiler
            if values is not None:
                self.items = values
            else:
                self.items = ChainMap()

        def new_child(self) -> Compiler.LLItems:
            return Compiler.LLItems(self.compiler, self.items.new_child())

        def get(self, item: ir.Value | Typ) -> ll.Value | ll.Type:
            if item not in self.items:
                if isinstance(item, ir.ComptimeValue):
                    self.items[item] = self.compiler._compile_comptime_value(item)
                elif isinstance(item, Typ):
                    self.items[item] = self.compiler._ll_typ(item)
            return self.items[item]

        def set(self, item: ir.Value | Typ, ll_item: ll.Value | ll.Type) -> None:
            self.items[item] = ll_item

    @dataclass
    class _FnBuilderContext:
        ll_builder: ll.IRBuilder
        ll_values: Compiler.LLItems
        ll_bbs: ChainMap[ir.BasicBlock, ll.Block]

    def __init__(self, mod: ir.Mod) -> None:
        self.mod = mod
        self.ll_mod = ll.Module(context=ll.Context())
        self._ll_mod_items = Compiler.LLItems(self)
        self._tmp_name = VarNamer()

    def compile(self) -> None:
        self.ll_mod.triple = "x86_64-linux-gnu"

        for item in self.mod.items:
            self._declare_mod_item(item)

        for item in self.mod.items:
            self._compile_mod_item(item)

    def _ll_typ(self, typ: Typ) -> ll.Type:
        match typ:
            case IntTyp():
                return ll.IntType(typ.width)
            case BoolTyp():
                return ll.IntType(1)
            case FnTyp():
                return ll.FunctionType(
                    self._ll_mod_items.get(typ.ret_typ),
                    (self._ll_mod_items.get(param_typ) for param_typ in typ.param_typs),
                )
            case PtrTyp():
                # Typed pointers are deprecated in llvmlite (and LLVM IR) but llvmlite seems to
                # rely on pointer types in a bunch of places (call instruction, gep instruction)
                # that's a pain to try to work around, so just use typed pointers for now.
                return ll.PointerType(pointee=self._ll_mod_items.get(typ.pointee_typ))
            case ArrayTyp():
                return ll.ArrayType(self._ll_mod_items.get(typ.element_typ), typ.length)
            case VoidTyp():
                return ll.VoidType()
            case _:
                raise NotImplementedError

    def _declare_mod_item(self, item: ir.ModItem):
        match item.value:
            case ir.ModVar():
                self._declare_mod_var(item, item.value)
            case ir.FnSpec():
                self._declare_mod_fn(item, item.value)
            case StructTyp():
                ll_item = self.ll_mod.context.get_identified_type(item.qualified_name)
                self._ll_mod_items.set(item.value, ll_item)
            case ir.Mod():
                self._declare_import(item, item.value)
            case _:
                raise NotImplementedError(item)

    def _compile_mod_item(self, item: ir.ModItem) -> None:
        match item.value:
            case ir.ModVar():
                return self._compile_mod_var(item, item.value)
            case ir.Fn():
                return self._compile_mod_fn(item, item.value)
            case ir.FnDecl():
                return
            case StructTyp():
                return self._compile_mod_struct(item, item.value)
            case ir.Mod():
                return self._compile_import(item, item.value)
            case _:
                raise NotImplementedError(item)

    def _declare_mod_var(self, item: ir.ModItem, var: ir.ModVar) -> ll.Value:
        ll_val = ll.GlobalVariable(
            self.ll_mod,
            self._ll_mod_items.get(var.typ.pointee_typ),
            item.qualified_name,
        )
        self._ll_mod_items.set(var, ll_val)
        set_linkage(ll_val, item.access)
        return ll_val

    def _compile_mod_var(self, _item: ir.ModItem, var: ir.ModVar) -> None:
        ll_init = self._ll_mod_items.get(var.initializer)
        self._ll_mod_items.get(var).initializer = ll_init  # type: ignore

    def _declare_mod_fn(self, item: ir.ModItem, fn: ir.FnSpec) -> ll.Value:
        ll_fn = ll.Function(
            self.ll_mod, self._ll_mod_items.get(fn.fn_typ), item.qualified_name
        )
        self._ll_mod_items.set(fn, ll_fn)
        # TODO: linkage for FnDecls?
        set_linkage(ll_fn, item.access)
        return ll_fn

    def _compile_mod_fn(self, _item: ir.ModItem, fn: ir.Fn) -> None:
        ctx = Compiler._FnBuilderContext(
            ll_builder=ll.IRBuilder(),
            ll_values=self._ll_mod_items.new_child(),
            ll_bbs=ChainMap(),
        )

        ll_fn = checked_cast(self._ll_mod_items.get(fn), ll.Function)
        for param, ll_param in zip(fn.params, ll_fn.args):
            ctx.ll_values.set(param, ll_param)

        bb_tree = bfs_tree(fn.cfg, fn.cfg.entry)
        for bb in bb_tree:
            if bb.name != "exit":
                ctx.ll_bbs[bb] = ll_fn.append_basic_block(bb.name)

        for bb in bb_tree:
            if bb.name != "exit":
                self._compile_bb(bb, ctx)

    def _compile_mod_struct(self, item: ir.ModItem, typ: StructTyp) -> None:
        ll_typ = self.ll_mod.context.get_identified_type(item.qualified_name)
        ll_typ.set_body(
            *(self._ll_mod_items.get(field.typ) for field in typ.fields.values())
        )

    def _declare_import(self, _item: ir.ModItem, mod: ir.Mod) -> None:
        for item in mod.items:
            if item.access == ir.PUBLIC:
                self._declare_mod_item(item)

    def _compile_import(self, _item: ir.ModItem, mod: ir.Mod) -> None:
        for item in mod.items:
            if item.access == ir.PUBLIC and isinstance(item.value, StructTyp):
                self._compile_mod_struct(item, item.value)

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
                    ctx.ll_values.get(instr.src), typ=self._ll_mod_items.get(instr.typ)
                )
            case ir.AllocaInstr():
                return ctx.ll_builder.alloca(
                    self._ll_mod_items.get(instr.allocated_typ)
                )
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
                ll_phi = ctx.ll_builder.phi(self._ll_mod_items.get(instr.typ))
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
                if instr.value.typ == VOID:
                    return ctx.ll_builder.ret_void()
                return ctx.ll_builder.ret(ctx.ll_values.get(instr.value))
            case ir.UnreachableInstr():
                return ctx.ll_builder.unreachable()
            case _:
                raise NotImplementedError(instr)

    def _compile_comptime_value(self, value: ir.ComptimeValue) -> ll.Value:
        match value:
            case ir.UndefValue():
                return ll.Constant(self._ll_mod_items.get(value.typ), ll.Undefined)
            case ir.VoidValue():
                raise NotImplementedError
            case ir.ComptimeInt():
                return ll.Constant(self._ll_mod_items.get(value.typ), value.value)
            case ir.ComptimeBool():
                return ll.Constant(
                    self._ll_mod_items.get(value.typ), 1 if value.value else 0
                )
            case ir.ComptimeCStr():
                ll_val = ll.GlobalVariable(
                    self.ll_mod,
                    self._ll_mod_items.get(value.initializer_typ),
                    self._tmp_name(".strlit"),
                )
                ll_val.initializer = ll.Constant(  # type: ignore
                    self._ll_mod_items.get(value.initializer_typ), value.value
                )
                ll_val.linkage = "private"
                # Cast from *[u8; N] to *u8 for C compatibility
                return ll_val.bitcast(self._ll_mod_items.get(value.typ))  # type: ignore
            case ir.ComptimeArray():
                elts = (self._ll_mod_items.get(elt) for elt in value.elements)
                return ll.Constant(self._ll_mod_items.get(value.typ), elts)
            case ir.ComptimeStruct():
                fields = (
                    self._ll_mod_items.get(value.fields[fname])
                    for fname in value.typ.fields
                )
                return ll.Constant(self._ll_mod_items.get(value.typ), fields)
            case ir.ComptimeGep():
                ll_indeces = [self._ll_mod_items.get(i) for i in value.indeces]
                base = self._ll_mod_items.get(value.base)
                assert isinstance(base, (ll.GlobalValue, ll.Constant))
                return base.gep(ll_indeces)
            case _:
                raise NotImplementedError(value)
