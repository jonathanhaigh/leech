"""Lowering of l0 IR to LLVM IR, via llvmlite."""

from collections import ChainMap
from dataclasses import dataclass
from typing import Optional
from llvmlite import ir as ll
from networkx import bfs_tree

from l0 import ir_module, ir_values
from l0.asserts import assert_eq, checked_cast
from l0.naming import VarNamer
from l0.typs import (
    USIZE,
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


def set_linkage(
    ll_global: ll.GlobalVariable | ll.Function, access: ir_module.Access
) -> None:
    """Set an LLVM global's linkage to match an l0 item's access.

    :param ll_global: The LLVM global variable or function to set linkage
        on.
    :param access: The l0 item's access.
    """
    if access == ir_module.PRIVATE:
        ll_global.linkage = "private"
    else:
        assert_eq(access, ir_module.PUBLIC)


class Compiler:
    """Lowers a single :class:`~l0.ir_module.Mod` to an LLVM module.

    :param mod: The IR module to compile.
    """

    mod: ir_module.Mod
    ll_mod: ll.Module
    _ll_mod_items: Compiler.LLItems
    _tmp_name: VarNamer

    class LLItems:
        """A cache mapping l0 IR values and types to their LLVM counterparts.

        Looking up an item that hasn't been compiled yet compiles and
        caches it on demand. Like :class:`~l0.ir_env.Env`, instances form
        a parent/child chain (see :meth:`new_child`), used to give each
        function body its own scope for its instructions' LLVM values
        while still sharing the enclosing module-level cache.

        :param compiler: The compiler to dispatch on-demand compilation
            to.
        :param values: The initial mapping to wrap, or ``None`` to start
            empty.
        """

        compiler: Compiler
        items: ChainMap[ir_values.Value | Typ, ll.Value | ll.Type]

        def __init__(
            self,
            compiler: Compiler,
            values: Optional[
                ChainMap[ir_values.Value | Typ, ll.Value | ll.Type]
            ] = None,
        ) -> None:
            super().__init__()
            self.compiler = compiler
            if values is not None:
                self.items = values
            else:
                self.items = ChainMap()

        def new_child(self) -> Compiler.LLItems:
            """Create a new cache nested inside this one.

            :return: A fresh cache that inherits this cache's entries.
            """
            return Compiler.LLItems(self.compiler, self.items.new_child())

        def get(self, item: ir_values.Value | Typ) -> ll.Value | ll.Type:
            """Get the LLVM value or type for ``item``, compiling it if needed.

            :param item: The l0 IR value or type to look up.
            :return: The corresponding LLVM value or type.
            """
            if item not in self.items:
                if isinstance(item, ir_values.ComptimeValue):
                    self.items[item] = self.compiler._compile_comptime_value(item)
                elif isinstance(item, Typ):
                    self.items[item] = self.compiler._ll_typ(item)
            return self.items[item]

        def set(self, item: ir_values.Value | Typ, ll_item: ll.Value | ll.Type) -> None:
            """Record the LLVM value or type to use for ``item``.

            :param item: The l0 IR value or type.
            :param ll_item: The LLVM value or type to associate with
                ``item``.
            """
            self.items[item] = ll_item

    @dataclass
    class _FnBuilderContext:
        ll_builder: ll.IRBuilder
        ll_values: Compiler.LLItems
        ll_bbs: ChainMap[ir_values.BasicBlock, ll.Block]

    def __init__(self, mod: ir_module.Mod) -> None:
        self.mod = mod
        self.ll_mod = ll.Module(context=ll.Context())
        self._ll_mod_items = Compiler.LLItems(self)
        self._tmp_name = VarNamer()

    def compile(self) -> None:
        """Compile :attr:`mod` into :attr:`ll_mod`.

        Every module item is declared (given an LLVM symbol) before any
        item is compiled, so that forward references between items
        resolve correctly regardless of declaration order.
        """
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

    def _declare_mod_item(self, item: ir_module.ModItem):
        match item.value:
            case ir_module.ModVar():
                self._declare_mod_var(item, item.value)
            case ir_module.FnSpec():
                self._declare_mod_fn(item, item.value)
            case StructTyp():
                ll_item = self.ll_mod.context.get_identified_type(item.qualified_name)
                self._ll_mod_items.set(item.value, ll_item)
            case ir_module.Mod():
                self._declare_import(item, item.value)
            case _:
                raise NotImplementedError(item)

    def _compile_mod_item(self, item: ir_module.ModItem) -> None:
        match item.value:
            case ir_module.ModVar():
                return self._compile_mod_var(item, item.value)
            case ir_module.Fn():
                return self._compile_mod_fn(item, item.value)
            case ir_module.FnDecl():
                return
            case StructTyp():
                return self._compile_mod_struct(item, item.value)
            case ir_module.Mod():
                return self._compile_import(item, item.value)
            case _:
                raise NotImplementedError(item)

    def _declare_mod_var(
        self, item: ir_module.ModItem, var: ir_module.ModVar
    ) -> ll.Value:
        ll_val = ll.GlobalVariable(
            self.ll_mod,
            self._ll_mod_items.get(var.typ.pointee_typ),
            item.qualified_name,
        )
        self._ll_mod_items.set(var, ll_val)
        set_linkage(ll_val, item.access)
        return ll_val

    def _compile_mod_var(self, _item: ir_module.ModItem, var: ir_module.ModVar) -> None:
        ll_init = self._ll_mod_items.get(var.initializer)
        self._ll_mod_items.get(var).initializer = ll_init  # type: ignore

    def _declare_mod_fn(
        self, item: ir_module.ModItem, fn: ir_module.FnSpec
    ) -> ll.Value:
        ll_fn = ll.Function(
            self.ll_mod, self._ll_mod_items.get(fn.fn_typ), item.qualified_name
        )
        self._ll_mod_items.set(fn, ll_fn)
        # TODO: linkage for FnDecls?
        set_linkage(ll_fn, item.access)
        return ll_fn

    def _compile_mod_fn(self, _item: ir_module.ModItem, fn: ir_module.Fn) -> None:
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

    def _compile_mod_struct(self, item: ir_module.ModItem, typ: StructTyp) -> None:
        ll_typ = self.ll_mod.context.get_identified_type(item.qualified_name)
        ll_typ.set_body(
            *(self._ll_mod_items.get(field.typ) for field in typ.fields.values())
        )

    def _declare_import(self, _item: ir_module.ModItem, mod: ir_module.Mod) -> None:
        for item in mod.items:
            if item.access == ir_module.PUBLIC:
                self._declare_mod_item(item)

    def _compile_import(self, _item: ir_module.ModItem, mod: ir_module.Mod) -> None:
        for item in mod.items:
            if item.access == ir_module.PUBLIC and isinstance(item.value, StructTyp):
                self._compile_mod_struct(item, item.value)

    def _compile_bb(
        self, bb: ir_values.BasicBlock, ctx: Compiler._FnBuilderContext
    ) -> None:
        ctx.ll_builder.position_at_start(ctx.ll_bbs[bb])
        for instr in bb.instrs:
            ctx.ll_values.set(instr, self._compile_instr(instr, ctx))

    def _compile_instr(
        self, instr: ir_values.Instr, ctx: Compiler._FnBuilderContext
    ) -> ll.Value:
        match instr:
            case ir_values.AddInstr():
                return ctx.ll_builder.add(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir_values.SubInstr():
                return ctx.ll_builder.sub(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir_values.MulInstr():
                return ctx.ll_builder.mul(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir_values.SdivInstr():
                return ctx.ll_builder.sdiv(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir_values.UdivInstr():
                return ctx.ll_builder.udiv(  # type: ignore
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir_values.IcmpSignedInstr():
                return ctx.ll_builder.icmp_signed(
                    instr.op,
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir_values.IcmpUnsignedInstr():
                return ctx.ll_builder.icmp_unsigned(
                    instr.op,
                    ctx.ll_values.get(instr.lhs),
                    ctx.ll_values.get(instr.rhs),
                )
            case ir_values.LoadInstr():
                return ctx.ll_builder.load(
                    ctx.ll_values.get(instr.src), typ=self._ll_mod_items.get(instr.typ)
                )
            case ir_values.AllocaInstr():
                return ctx.ll_builder.alloca(
                    self._ll_mod_items.get(instr.allocated_typ)
                )
            case ir_values.StoreInstr():
                return ctx.ll_builder.store(
                    ctx.ll_values.get(instr.value),
                    ctx.ll_values.get(instr.dest),
                )
            case ir_values.GepInstr():
                zero = ll.Constant(self._ll_mod_items.get(USIZE), 0)
                ll_index = ctx.ll_values.get(instr.index)
                return ctx.ll_builder.gep(
                    ctx.ll_values.get(instr.base), [zero, ll_index]
                )
            case ir_values.InsertValueInstr():
                return ctx.ll_builder.insert_value(
                    ctx.ll_values.get(instr.aggregate),
                    ctx.ll_values.get(instr.value),
                    [index.value for index in instr.indeces],
                )
            case ir_values.CallInstr():
                ll_args = [ctx.ll_values.get(arg) for arg in instr.args]
                return ctx.ll_builder.call(ctx.ll_values.get(instr.callee), ll_args)
            case ir_values.PhiInstr():
                ll_phi = ctx.ll_builder.phi(self._ll_mod_items.get(instr.typ))
                for bb, value in instr.incoming.items():
                    ll_phi.add_incoming(ctx.ll_values.get(value), ctx.ll_bbs[bb])
                return ll_phi
            case ir_values.BranchInstr():
                return ctx.ll_builder.branch(ctx.ll_bbs[instr.target])
            case ir_values.CbranchInstr():
                return ctx.ll_builder.cbranch(
                    ctx.ll_values.get(instr.condition),
                    ctx.ll_bbs[instr.true_target],
                    ctx.ll_bbs[instr.false_target],
                )
            case ir_values.RetInstr():
                if instr.value.typ == VOID:
                    return ctx.ll_builder.ret_void()
                return ctx.ll_builder.ret(ctx.ll_values.get(instr.value))
            case ir_values.UnreachableInstr():
                return ctx.ll_builder.unreachable()
            case _:
                raise NotImplementedError(instr)

    def _compile_comptime_value(self, value: ir_values.ComptimeValue) -> ll.Value:
        match value:
            case ir_values.UndefValue():
                return ll.Constant(self._ll_mod_items.get(value.typ), ll.Undefined)
            case ir_values.VoidValue():
                raise NotImplementedError
            case ir_values.ComptimeInt():
                return ll.Constant(self._ll_mod_items.get(value.typ), value.value)
            case ir_values.ComptimeBool():
                return ll.Constant(
                    self._ll_mod_items.get(value.typ), 1 if value.value else 0
                )
            case ir_values.ComptimeCStr():
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
            case ir_values.ComptimeArray():
                elts = (self._ll_mod_items.get(elt) for elt in value.elements)
                return ll.Constant(self._ll_mod_items.get(value.typ), elts)
            case ir_values.ComptimeStruct():
                fields = (
                    self._ll_mod_items.get(value.fields[fname])
                    for fname in value.typ.fields
                )
                return ll.Constant(self._ll_mod_items.get(value.typ), fields)
            case ir_values.ComptimeGep():
                zero = ll.Constant(self._ll_mod_items.get(USIZE), 0)
                ll_index = self._ll_mod_items.get(value.index)
                base = self._ll_mod_items.get(value.base)
                assert isinstance(base, (ll.GlobalValue, ll.Constant))
                return base.gep([zero, ll_index])
            case _:
                raise NotImplementedError(value)
