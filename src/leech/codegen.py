# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Lowering of Leech IR to LLVM IR, via llvmlite."""

import collections
import dataclasses
from collections.abc import Iterator
from typing import Final, Optional

import networkx as nx
from llvmlite import ir as ll

from leech import asserts, ir_module, ir_traits, ir_values, mono, naming, signage, typs, visibility

_BIN_OP_LL_METHODS: Final[dict[type[ir_values.BinOpInstr], str]] = {
    ir_values.AddInstr: "add",
    ir_values.SubInstr: "sub",
    ir_values.MulInstr: "mul",
    ir_values.SdivInstr: "sdiv",
    ir_values.UdivInstr: "udiv",
}

_UNARY_OP_LL_METHODS: Final[dict[type[ir_values.Instr], str]] = {
    ir_values.NegInstr: "neg",
    ir_values.NotInstr: "not_",
}

_OVERFLOW_INTRINSIC_PREFIXES: Final[dict[tuple[type[ir_values.Instr], signage.Signage], str]] = {
    (ir_values.CheckedAddInstr, signage.SIGNED): "sadd",
    (ir_values.CheckedAddInstr, signage.UNSIGNED): "uadd",
    (ir_values.CheckedSubInstr, signage.SIGNED): "ssub",
    (ir_values.CheckedSubInstr, signage.UNSIGNED): "usub",
    (ir_values.CheckedMulInstr, signage.SIGNED): "smul",
    (ir_values.CheckedMulInstr, signage.UNSIGNED): "umul",
}


def _set_linkage(ll_global: ll.GlobalVariable | ll.Function, access: visibility.Access) -> None:
    """Set an LLVM global's linkage from a Leech access level."""
    if access == visibility.PRIVATE:
        ll_global.linkage = "private"
    else:
        asserts.assert_eq(access, visibility.PUBLIC)


class Compiler:
    """Lower one Leech IR module to LLVM IR."""

    _mod: Final[ir_module.Mod]
    ll_mod: Final[ll.Module]
    _ll_mod_items: Final[Compiler._LLItems]
    _tmp_name: Final[naming.VarNamer]
    _overflow_intrinsics: Final[dict[str, ll.Function]]

    class _LLItems:
        """A nested, lazily compiled mapping from Leech IR items to LLVM items."""

        _compiler: Final[Compiler]
        _items: Final[collections.ChainMap[ir_values.Value | typs.Typ, ll.Value | ll.Type]]

        def __init__(
            self,
            compiler: Compiler,
            values: Optional[
                collections.ChainMap[ir_values.Value | typs.Typ, ll.Value | ll.Type]
            ] = None,
        ) -> None:
            super().__init__()
            self._compiler = compiler
            if values is not None:
                self._items = values
            else:
                self._items = collections.ChainMap()

        def new_child(self) -> Compiler._LLItems:
            """Create a child cache that inherits this cache's entries."""
            return Compiler._LLItems(self._compiler, self._items.new_child())

        def __contains__(self, item: ir_values.Value | typs.Typ) -> bool:
            return item in self._items

        def get(self, item: ir_values.Value | typs.Typ) -> ll.Value | ll.Type:
            """Return ``item``'s LLVM counterpart, compiling it when needed."""
            try:
                return self._items[item]
            except KeyError:
                if isinstance(item, ir_values.ComptimeValue):
                    compiled = self._compiler._compile_comptime_value(item)
                elif isinstance(item, typs.Typ):
                    compiled = self._compiler._ll_typ(item)
                else:
                    raise
                self._items[item] = compiled
                return compiled

        def set(self, item: ir_values.Value | typs.Typ, ll_item: ll.Value | ll.Type) -> None:
            """Associate ``item`` with its LLVM counterpart."""
            self._items[item] = ll_item

    @dataclasses.dataclass(frozen=True)
    class _FnBuilderContext:
        ll_builder: ll.IRBuilder
        ll_values: Compiler._LLItems
        ll_bbs: collections.ChainMap[ir_values.BasicBlock, ll.Block]
        #: Raw overflow-intrinsic results shared with their paired flag instructions.
        overflow_calls: dict[ir_values.CheckedBinOpInstr, ll.Value]

    def __init__(self, mod: ir_module.Mod) -> None:
        self._mod = mod
        self.ll_mod = ll.Module(context=ll.Context())
        self._ll_mod_items = Compiler._LLItems(self)
        self._tmp_name = naming.VarNamer()
        self._overflow_intrinsics = {}

    def compile(self) -> None:
        """Compile declarations before bodies and discover generic instances to a fixpoint.

        Imported functions remain declarations, but imported struct layouts are defined locally.
        Generic templates have no LLVM representation; only their reachable instances do.
        """
        self.ll_mod.triple = "x86_64-linux-gnu"

        for item in self._program_items():
            if isinstance(item.value, typs.StructTyp):
                self._declare_mod_item(item)

        for item in self._program_items():
            if not isinstance(item.value, (typs.StructTyp, typs.StructTypTemplate)):
                self._declare_mod_item(item)

        for item in self._program_items():
            if isinstance(item.value, typs.StructTyp):
                self._compile_mod_struct(item, item.value)

        # A generic struct's own fields are never lowered - only an
        # instantiation's are, below - but they're still validated here,
        # against the struct's own (opaque) comptime parameters, the same as
        # every other declared struct's: an infinite-size struct is
        # rejected whether or not anything in the program instantiates it.
        #
        # An enum has nothing to declare or compile (see _ll_typ's EnumTyp
        # case), so it's forced here purely to validate it (duplicate
        # variants, a non-integer or overflowing explicit backing type)
        # whether or not the program ever references it, the same as a
        # generic struct's. backing_typ itself reads every variant, so
        # forcing it alone already forces (and catches duplicates in)
        # variants too.
        for item in self._program_items():
            if isinstance(item.value, typs.StructTypTemplate):
                item.value.validate_declaration()
            elif isinstance(item.value, typs.EnumTyp):
                _ = item.value.backing_typ

        result = mono.discover(self._mod)
        for struct_inst in result.struct_instances:
            self._declare_struct_instance(struct_inst)
        for struct_inst in result.struct_instances:
            self._compile_struct_instance(struct_inst)

        for inst in result.fn_instances:
            self._declare_fn_instance(inst)
        for inst in result.imported_fn_instances:
            self._declare_fn_instance(inst)

        for item in self._mod.items:
            self._compile_mod_item(item)

        for inst in result.fn_instances:
            self._compile_fn_instance(inst)

    def _program_items(self) -> Iterator[ir_module.ModItem]:
        """Yield local items and public imported items in module load order."""
        for mod in self._mod.loader.mods:
            for item in mod.items:
                if isinstance(item.value, ir_module.Mod):
                    continue
                if mod is self._mod or item.access == visibility.PUBLIC:
                    yield item

    def _declare_struct_instance(self, inst: typs.StructTyp) -> None:
        ll_typ = self.ll_mod.context.get_identified_type(inst.qualified_name)
        self._ll_mod_items.set(inst, ll_typ)

    def _compile_struct_instance(self, inst: typs.StructTyp) -> None:
        ll_typ = self.ll_mod.context.get_identified_type(inst.qualified_name)
        ll_typ.set_body(*(self._ll_mod_items.get(field.typ) for field in inst.fields.values()))

    def _ll_typ(self, typ: typs.Typ) -> ll.Type:
        match typ:
            case typs.IntTyp():
                return ll.IntType(typ.width)
            case typs.BoolTyp():
                return ll.IntType(1)
            case typs.FnTyp():
                # A `never` return type has no LLVM representation of its
                # own (see the NeverTyp case below): a function declared
                # to return `never` only ever ends its body in
                # `unreachable`, so its LLVM signature can say `void`
                # without a real `ret` instruction ever needing to match
                # it.
                ll_ret_typ = (
                    ll.VoidType()
                    if isinstance(typ.ret_typ, typs.NeverTyp)
                    else self._ll_mod_items.get(typ.ret_typ)
                )
                return ll.FunctionType(
                    ll_ret_typ,
                    (self._ll_mod_items.get(param_typ) for param_typ in typ.param_typs),
                )
            case typs.PtrTyp():
                # Typed pointers are deprecated in llvmlite (and LLVM IR) but
                # llvmlite seems to rely on pointer types in a bunch of places
                # (call instruction, gep instruction) that's a pain to try to
                # work around, so just use typed pointers for now.
                return ll.PointerType(pointee=self._ll_mod_items.get(typ.pointee_typ))
            case typs.ArrayTyp():
                return ll.ArrayType(
                    self._ll_mod_items.get(typ.element_typ),
                    asserts.checked_cast(typ.length, typs.ComptimeValueTyp).value,
                )
            case typs.VoidTyp():
                return ll.VoidType()
            case typs.EnumTyp():
                # No LLVM type of its own - an enum lowers directly to its
                # backing integer type's.
                return asserts.checked_cast(self._ll_mod_items.get(typ.backing_typ), ll.Type)
            case typs.StructTyp():
                # Every StructTyp reachable during codegen is declared
                # (and cached here) by the earlier declare-types phase,
                # so _LLItems.get() should never fall through to this
                # method for one - if it does, that phase missed it.
                assert typ in self._ll_mod_items, f'struct "{typ.name}" was never declared'
                return asserts.checked_cast(self._ll_mod_items.get(typ), ll.Type)
            case typs.NeverTyp():
                raise AssertionError("a never-typed value shouldn't need an LLVM type")
            case _:
                raise AssertionError(f"unhandled type {typ}")

    def _declare_mod_item(self, item: ir_module.ModItem):
        match item.value:
            case ir_module.ModVar():
                self._declare_mod_var(item, item.value)
            case ir_module.ExternFnSymbol():
                self._declare_mod_fn(item, item.value)
            case ir_module.FnSymbol():
                pass
            case typs.StructTyp():
                ll_item = self.ll_mod.context.get_identified_type(item.qualified_name)
                self._ll_mod_items.set(item.value, ll_item)
            case typs.StructTypTemplate():
                pass
            case typs.EnumTyp():
                # No LLVM symbol of its own - it lowers directly to its
                # backing integer type's, declared (if a builtin) already.
                pass
            case ir_traits.Trait():
                # A declaration, not a value or type with anything of its
                # own to declare - only a trait impl's methods, registered
                # as their own ordinary items, need one.
                pass
            case _:
                # An import (ir_module.Mod) is the only other possible
                # item value, and _program_items() - the only source of
                # items passed here - filters those out.
                raise AssertionError(f"unhandled module item {item}")

    def _compile_mod_item(self, item: ir_module.ModItem) -> None:
        match item.value:
            case ir_module.ModVar():
                return self._compile_mod_var(item, item.value)
            case ir_module.FnSymbol():
                return None
            case typs.StructTyp():
                # Already compiled, along with every other module's, by
                # the struct-body phase of compile().
                return None
            case typs.StructTypTemplate():
                return None
            case typs.EnumTyp():
                return None
            case ir_module.Mod():
                # An import contributes no symbols of its own; the
                # imported module's items are handled by compile()'s walk
                # over the whole program.
                return None
            case ir_traits.Trait():
                # A declaration, not a value with a body to compile.
                return None
            case _:
                raise AssertionError(f"unhandled module item {item}")

    def _declare_mod_var(self, item: ir_module.ModItem, var: ir_module.ModVar) -> ll.Value:
        ll_val = ll.GlobalVariable(
            self.ll_mod,
            self._ll_mod_items.get(var.typ.pointee_typ),
            item.qualified_name,
        )
        self._ll_mod_items.set(var, ll_val)
        _set_linkage(ll_val, item.access)
        return ll_val

    def _compile_mod_var(self, _item: ir_module.ModItem, var: ir_module.ModVar) -> None:
        ll_init = self._ll_mod_items.get(var.initializer)
        self._ll_mod_items.get(var).initializer = ll_init  # type: ignore

    def _declare_mod_fn(self, item: ir_module.ModItem, fn: ir_module.ExternFnSymbol) -> ll.Value:
        inst = fn.instantiate(())
        ll_fn = ll.Function(self.ll_mod, self._ll_mod_items.get(inst.fn_typ), inst.qualified_name)
        self._ll_mod_items.set(inst.ref, ll_fn)
        _set_linkage(ll_fn, item.access)
        return ll_fn

    def _declare_fn_instance(self, inst: ir_module.FnInstance) -> ll.Value:
        ll_fn = ll.Function(self.ll_mod, self._ll_mod_items.get(inst.fn_typ), inst.qualified_name)
        self._ll_mod_items.set(inst.ref, ll_fn)
        if inst.uses_generic_linkage:
            # A specialization may be emitted again, identically,
            # wherever else it's called. linkonce_odr lets the linker
            # keep one interchangeable copy.
            ll_fn.linkage = "linkonce_odr"
        else:
            src_fn = inst.src_fn
            assert src_fn is not None
            _set_linkage(ll_fn, src_fn.access)
        return ll_fn

    def _compile_fn_instance(self, inst: ir_module.FnInstance) -> None:
        """Compile an instance's lowered body into its declared LLVM function."""
        ctx = Compiler._FnBuilderContext(
            ll_builder=ll.IRBuilder(),
            ll_values=self._ll_mod_items.new_child(),
            ll_bbs=collections.ChainMap(),
            overflow_calls={},
        )

        ll_fn = asserts.checked_cast(self._ll_mod_items.get(inst.ref), ll.Function)
        for param, ll_param in zip(inst.params, ll_fn.args, strict=True):
            ctx.ll_values.set(param, ll_param)

        # Reverse postorder, not a plain BFS: a phi's block must be
        # compiled after every block that produces one of its incoming
        # values, and BFS (ordering by shortest distance from entry)
        # doesn't guarantee that whenever two merging branches reach
        # their merge block at unequal depths - e.g. a short-circuit
        # operator's direct edge into its end block versus a longer
        # chain evaluating its right operand. RPO guarantees it for any
        # edge that isn't a loop back-edge, and loop back-edges never
        # carry a phi in this compiler (`while` produces no merged
        # value), so this is safe even though `cfg` can have cycles.
        bb_order = reversed(list(nx.dfs_postorder_nodes(inst.cfg, inst.cfg.entry)))
        bb_order = [bb for bb in bb_order if bb.name != "exit"]
        for bb in bb_order:
            ctx.ll_bbs[bb] = ll_fn.append_basic_block(bb.name)

        for bb in bb_order:
            self._compile_bb(bb, ctx)

    def _compile_mod_struct(self, item: ir_module.ModItem, typ: typs.StructTyp) -> None:
        ll_typ = self.ll_mod.context.get_identified_type(item.qualified_name)
        ll_typ.set_body(*(self._ll_mod_items.get(field.typ) for field in typ.fields.values()))

    def _compile_bb(self, bb: ir_values.BasicBlock, ctx: Compiler._FnBuilderContext) -> None:
        ctx.ll_builder.position_at_start(ctx.ll_bbs[bb])
        for instr in bb.instrs:
            ctx.ll_values.set(instr, self._compile_instr(instr, ctx))

    def _overflow_intrinsic(
        self, instr_typ: type[ir_values.Instr], typ: typs.IntTyp
    ) -> ll.Function:
        """Get (declaring, and caching, on first use) the LLVM intrinsic
        function that computes ``instr_typ``'s operation on ``typ`` and
        detects overflow in one call.

        Declared to return ``{typ, i1}``: the operation's own result
        alongside the overflow flag - see the
        :class:`~leech.ir_values.CheckedAddInstr`/``CheckedSubInstr``/
        ``CheckedMulInstr`` case in :meth:`_compile_instr`, which reads
        both back out (via ``extractvalue``, once each, from its shared
        call result) rather than computing the operation separately.

        :param instr_typ: Which checked instruction to get the intrinsic
            for: :class:`~leech.ir_values.CheckedAddInstr`, ``CheckedSubInstr``,
            or ``CheckedMulInstr``.
        :param typ: The operands' type.
        :return: The (possibly newly-declared) intrinsic function.
        """
        prefix = _OVERFLOW_INTRINSIC_PREFIXES[(instr_typ, typ.signage)]
        name = f"llvm.{prefix}.with.overflow.i{typ.width}"
        intrinsic = self._overflow_intrinsics.get(name)
        if intrinsic is None:
            ll_typ = self._ll_mod_items.get(typ)
            fn_typ = ll.FunctionType(
                ll.LiteralStructType([ll_typ, ll.IntType(1)]), [ll_typ, ll_typ]
            )
            intrinsic = ll.Function(self.ll_mod, fn_typ, name)
            self._overflow_intrinsics[name] = intrinsic
        return intrinsic

    def _compile_instr(self, instr: ir_values.Instr, ctx: Compiler._FnBuilderContext) -> ll.Value:
        match instr:
            case (
                ir_values.CheckedAddInstr()
                | ir_values.CheckedSubInstr()
                | ir_values.CheckedMulInstr()
            ):
                typ = asserts.checked_cast(instr.lhs.typ, typs.IntTyp)
                intrinsic = self._overflow_intrinsic(type(instr), typ)
                call = ctx.ll_builder.call(
                    intrinsic, [ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs)]
                )
                ctx.overflow_calls[instr] = call
                return ctx.ll_builder.extract_value(call, 0)  # type: ignore
            case ir_values.BinOpInstr():
                ll_method = getattr(ctx.ll_builder, _BIN_OP_LL_METHODS[type(instr)])
                return ll_method(ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs))
            case ir_values.NegInstr() | ir_values.NotInstr():
                ll_method = getattr(ctx.ll_builder, _UNARY_OP_LL_METHODS[type(instr)])
                return ll_method(ctx.ll_values.get(instr.operand))
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
            case ir_values.OverflowFlagInstr():
                call = ctx.overflow_calls[instr.checked_op]
                return ctx.ll_builder.extract_value(call, 1)  # type: ignore
            case ir_values.LoadInstr():
                return ctx.ll_builder.load(
                    ctx.ll_values.get(instr.src), typ=self._ll_mod_items.get(instr.typ)
                )
            case ir_values.IntExtInstr():
                # Which extension to use follows from the source type: a
                # signed value's sign bit has to be replicated to keep
                # its value, an unsigned one's must not be.
                src_typ = asserts.checked_cast(instr.value.typ, typs.IntTyp)
                extend = (
                    ctx.ll_builder.sext
                    if src_typ.signage == signage.SIGNED
                    else ctx.ll_builder.zext
                )
                return extend(  # type: ignore
                    ctx.ll_values.get(instr.value),
                    self._ll_mod_items.get(instr.typ),
                )
            case ir_values.AllocaInstr():
                return ctx.ll_builder.alloca(self._ll_mod_items.get(instr.allocated_typ))
            case ir_values.StoreInstr():
                return ctx.ll_builder.store(
                    ctx.ll_values.get(instr.value),
                    ctx.ll_values.get(instr.dest),
                )
            case ir_values.GepInstr():
                zero = ll.Constant(self._ll_mod_items.get(typs.USIZE), 0)
                ll_index = ctx.ll_values.get(instr.index)
                return ctx.ll_builder.gep(ctx.ll_values.get(instr.base), [zero, ll_index])
            case ir_values.SizeOfInstr():
                # The standard portable idiom for sizeof(T): index one T
                # past a null T* and read the resulting address, so LLVM's
                # own (platform-dependent, padding-including) layout rules
                # compute the size, rather than reimplementing them here.
                ll_ptr_typ = self._ll_mod_items.get(
                    typs.PtrTyp.get_or_create(instr.sized_typ, typs.CONST)
                )
                null_ptr = ll.Constant(ll_ptr_typ, None)
                one = ll.Constant(self._ll_mod_items.get(typs.USIZE), 1)
                elt_ptr = ctx.ll_builder.gep(null_ptr, [one])
                return ctx.ll_builder.ptrtoint(  # type: ignore
                    elt_ptr, self._ll_mod_items.get(typs.USIZE)
                )
            case ir_values.PtrCastInstr():
                return ctx.ll_builder.bitcast(  # type: ignore
                    ctx.ll_values.get(instr.operand), self._ll_mod_items.get(instr.target_typ)
                )
            case ir_values.IsNullInstr():
                null_ptr = ll.Constant(self._ll_mod_items.get(instr.operand.typ), None)
                return ctx.ll_builder.icmp_unsigned(
                    "==", ctx.ll_values.get(instr.operand), null_ptr
                )
            case ir_values.EnumToIntInstr():
                # A no-op: an enum's LLVM type already is its backing
                # type's, so the operand's own compiled value already is
                # the answer.
                return asserts.checked_cast(ctx.ll_values.get(instr.operand), ll.Value)
            case ir_values.InsertValueInstr():
                return ctx.ll_builder.insert_value(
                    ctx.ll_values.get(instr.aggregate),
                    ctx.ll_values.get(instr.value),
                    instr.index.value,
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
                if instr.value.typ == typs.VOID:
                    return ctx.ll_builder.ret_void()
                return ctx.ll_builder.ret(ctx.ll_values.get(instr.value))
            case ir_values.UnreachableInstr():
                return ctx.ll_builder.unreachable()
            case _:
                raise AssertionError(f"unhandled instruction {instr}")

    def _compile_comptime_value(self, value: ir_values.ComptimeValue) -> ll.Value:
        match value:
            case ir_values.UndefValue():
                return ll.Constant(self._ll_mod_items.get(value.typ), ll.Undefined)
            case ir_values.VoidValue():
                raise AssertionError("a void value should never need a runtime representation")
            case ir_values.ComptimeInt():
                return ll.Constant(self._ll_mod_items.get(value.typ), value.value)
            case ir_values.ComptimeEnum():
                return ll.Constant(self._ll_mod_items.get(value.typ), value.value)
            case ir_values.ComptimeBool():
                return ll.Constant(self._ll_mod_items.get(value.typ), 1 if value.value else 0)
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
                fields = (self._ll_mod_items.get(value.fields[fname]) for fname in value.typ.fields)
                return ll.Constant(self._ll_mod_items.get(value.typ), fields)
            case ir_values.ComptimeGep():
                zero = ll.Constant(self._ll_mod_items.get(typs.USIZE), 0)
                ll_index = self._ll_mod_items.get(value.index)
                base = self._ll_mod_items.get(value.base)
                assert isinstance(base, (ll.GlobalValue, ll.Constant))
                return base.gep([zero, ll_index])
            case _:
                # FnRefs are mapped during function declaration, before any
                # initializer or body can request one, so they never reach this
                # fallback. Of the remaining subclasses, a NeverValue can only
                # exist in an already-terminated block, and a ComptimeAlloc
                # cannot escape the interpreter because it is temporary.
                raise AssertionError(f"unhandled comptime value {value}")
