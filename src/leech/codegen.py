# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Lowering of Leech IR to LLVM IR, via llvmlite."""

import collections
import dataclasses
from collections.abc import Iterator, Sequence
from typing import Final, Optional, cast

import networkx as nx
from llvmlite import ir as ll

from leech import (
    asserts,
    ir_module,
    ir_traits,
    ir_values,
    ll_layout,
    ll_typs,
    mono,
    naming,
    signage,
    target,
    typs,
    visibility,
)


def _contains_union(typ: typs.Typ) -> bool:
    """Whether a union is reachable within ``typ`` without passing a pointer.

    Which is exactly when a constant of ``typ`` cannot use ``typ``'s own
    LLVM representation, since the union's bytes sit inline in whatever
    contains it. A pointer ends the walk, holding an address rather than
    those bytes, which is also what keeps this terminating: a by-value
    cycle is rejected long before codegen.
    """
    match typ:
        case typs.UnionTyp():
            return True
        case typs.StructTyp():
            return any(_contains_union(field.typ) for field in typ.fields.values())
        case typs.ArrayTyp():
            return _contains_union(typ.element_typ)
        case _:
            return False


def _set_linkage(ll_global: ll.GlobalVariable | ll.Function, access: visibility.Access) -> None:
    """Set an LLVM global's linkage from a Leech access level."""
    if access == visibility.PRIVATE:
        ll_global.linkage = "private"
    else:
        asserts.assert_eq(access, visibility.PUBLIC)


def _ll_typ_of(ll_value: ll.Value) -> ll.Type:
    """An LLVM value's own type, which llvmlite sets per instance rather than declaring."""
    return asserts.checked_cast(ll_value.type, ll.Type)  # type: ignore


def _checked_builder_value(value: Optional[ll.Value]) -> ll.Value:
    # Some llvmlite instruction builders are annotated as optionally returning a value.
    return asserts.checked_cast(value, ll.Value)


class Compiler:
    """Lower one Leech IR module to LLVM IR."""

    _mod: Final[ir_module.Mod]
    ll_mod: Final[ll.Module]
    _ll_mod_items: Final[Compiler._LLItems]
    _tmp_name: Final[naming.VarNamer]
    _overflow_intrinsics: Final[dict[str, ll.Function]]
    #: Raw globals, kept apart from the item values so a module variable
    #: whose value is a bitcast can still take an initializer and linkage.
    _mod_var_globals: Final[dict[ir_module.ModVar, ll.GlobalVariable]]

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
                    compiled = self._compiler._ll_typ(cast(typs.TypKind, item))
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
        self._mod_var_globals = {}

    def compile(self) -> None:
        """Compile declarations before bodies and discover generic instances to a fixpoint.

        Imported functions remain declarations, but imported struct layouts are defined locally.
        Generic templates have no LLVM representation; only their reachable instances do.
        """
        self.ll_mod.triple = target.TRIPLE
        self.ll_mod.data_layout = target.DATALAYOUT

        for item in self._program_items():
            if isinstance(item.value, typs.StructTyp | typs.UnionTyp):
                self._declare_mod_item(item)

        for item in self._program_items():
            if not isinstance(
                item.value,
                typs.StructTyp | typs.StructTypTemplate | typs.UnionTyp | typs.UnionTypTemplate,
            ):
                self._declare_mod_item(item)

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
            if isinstance(item.value, typs.StructTypTemplate | typs.UnionTypTemplate):
                item.value.validate_declaration()
            elif isinstance(item.value, typs.EnumTyp):
                _ = item.value.backing_typ

        result = mono.discover(self._mod)
        for struct_inst in result.struct_instances:
            self._declare_nominal_instance(struct_inst)
        for union_inst in result.union_instances:
            self._declare_nominal_instance(union_inst)

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

    def _declare_nominal_instance(self, inst: typs.StructTyp | typs.UnionTyp) -> None:
        self._ll_mod_items.get(inst)

    def _ll_typ(self, typ: typs.TypKind) -> ll.Type:
        return ll_typs.ll_typ(self.ll_mod.context, typ)

    def _declare_mod_item(self, item: ir_module.ModItem):
        match item.value:
            case ir_module.ModVar():
                self._declare_mod_var(item, item.value)
            case ir_module.ExternFnSymbol():
                self._declare_mod_fn(item, item.value)
            case ir_module.FnSymbol():
                pass
            case typs.StructTyp():
                self._ll_mod_items.get(item.value)
            case typs.StructTypTemplate():
                pass
            case typs.UnionTyp():
                self._ll_mod_items.get(item.value)
            case typs.UnionTypTemplate():
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
            case ir_module.Mod():
                # _program_items() filters imports before this method.
                raise AssertionError(f"import reached module-item declaration: {item}")

    def _compile_mod_item(self, item: ir_module.ModItem) -> None:
        match item.value:
            case ir_module.ModVar():
                return self._compile_mod_var(item, item.value)
            case ir_module.FnSymbol():
                return None
            case typs.StructTyp():
                # Already fully built by the earlier declare-structs pass
                # of compile().
                return None
            case typs.StructTypTemplate():
                return None
            case typs.UnionTyp() | typs.UnionTypTemplate():
                # Already fully built by the earlier declare-types pass.
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

    def _declare_mod_var(self, item: ir_module.ModItem, var: ir_module.ModVar) -> ll.Value:
        """Declare a module variable's global, with the layout its initializer needs.

        A constant reaching a union cannot use the union's own LLVM type,
        so the global takes an ad-hoc one. Only that type is derived here,
        never the constant: the constant is another module's to build when
        the variable is imported, and building one would need globals this
        pass has not reached.
        """
        declared_ll_typ = asserts.checked_cast(self._ll_mod_items.get(var.typ.pointee_typ), ll.Type)
        ll_typ = self._target_layout_typ(var.initializer)
        ll_val = ll.GlobalVariable(self.ll_mod, ll_typ, item.qualified_name)
        self._mod_var_globals[var] = ll_val
        _set_linkage(ll_val, item.access)

        if ll_typ == declared_ll_typ:
            self._ll_mod_items.set(var, ll_val)
            return ll_val

        # A packed ad-hoc type has alignment 1, so the union's own
        # requirement has to be stated. It comes from the declared outer
        # type: a struct holding both an i128 and a weakly aligned union
        # would otherwise take the union's figure and be under-aligned.
        ll_val.align = self._align(declared_ll_typ)  # type: ignore
        # Only the raw global can take an initializer or linkage, so the
        # bitcast is registered as the value everything else consumes.
        self._ll_mod_items.set(
            var,
            ll_val.bitcast(ll.PointerType(declared_ll_typ)),  # type: ignore
        )
        return ll_val

    def _compile_mod_var(self, _item: ir_module.ModItem, var: ir_module.ModVar) -> None:
        ll_val = self._mod_var_globals[var]
        initializer = self._target_layout_constant(var.initializer)
        # The global's type was derived without building this, so the two
        # derivations have to agree about it.
        asserts.assert_eq(str(_ll_typ_of(initializer)), str(ll_val.type.pointee))  # type: ignore
        ll_val.initializer = initializer  # type: ignore

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

    def _compile_bb(self, bb: ir_values.BasicBlock, ctx: Compiler._FnBuilderContext) -> None:
        ctx.ll_builder.position_at_start(ctx.ll_bbs[bb])
        for instr in bb.instrs:
            ctx.ll_values.set(instr, self._compile_instr(instr, ctx))

    def _overflow_intrinsic(
        self, instr: ir_values.CheckedBinOpInstr, typ: typs.IntTyp
    ) -> ll.Function:
        """Get (declaring, and caching, on first use) the LLVM intrinsic
        function that computes ``instr``'s operation on ``typ`` and
        detects overflow in one call.

        Declared to return ``{typ, i1}``: the operation's own result
        alongside the overflow flag - see the
        ``ir_values.CheckedAddInstr``/``CheckedSubInstr``/
        ``CheckedMulInstr`` case in ``_compile_instr``, which reads
        both back out (via ``extractvalue``, once each, from its shared
        call result) rather than computing the operation separately.
        """
        match instr:
            case ir_values.CheckedAddInstr():
                op = "add"
            case ir_values.CheckedSubInstr():
                op = "sub"
            case ir_values.CheckedMulInstr():
                op = "mul"
        match typ.signage:
            case signage.Signage.SIGNED:
                prefix = f"s{op}"
            case signage.Signage.UNSIGNED:
                prefix = f"u{op}"
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

    def _compile_instr(
        self, instr: ir_values.InstrKind, ctx: Compiler._FnBuilderContext
    ) -> ll.Value:
        match instr:
            case (
                ir_values.CheckedAddInstr()
                | ir_values.CheckedSubInstr()
                | ir_values.CheckedMulInstr()
            ):
                typ = asserts.checked_cast(instr.lhs.typ, typs.IntTyp)
                intrinsic = self._overflow_intrinsic(instr, typ)
                call = ctx.ll_builder.call(
                    intrinsic, [ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs)]
                )
                ctx.overflow_calls[instr] = call
                return ctx.ll_builder.extract_value(call, 0)  # type: ignore
            case ir_values.AddInstr():
                return _checked_builder_value(
                    ctx.ll_builder.add(ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs))
                )
            case ir_values.SubInstr():
                return _checked_builder_value(
                    ctx.ll_builder.sub(ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs))
                )
            case ir_values.MulInstr():
                return _checked_builder_value(
                    ctx.ll_builder.mul(ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs))
                )
            case ir_values.SdivInstr():
                return _checked_builder_value(
                    ctx.ll_builder.sdiv(ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs))
                )
            case ir_values.UdivInstr():
                return _checked_builder_value(
                    ctx.ll_builder.udiv(ctx.ll_values.get(instr.lhs), ctx.ll_values.get(instr.rhs))
                )
            case ir_values.NegInstr():
                return _checked_builder_value(ctx.ll_builder.neg(ctx.ll_values.get(instr.operand)))
            case ir_values.NotInstr():
                return _checked_builder_value(ctx.ll_builder.not_(ctx.ll_values.get(instr.operand)))
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
                # instr.sized_typ is always concrete by codegen time (the
                # same guarantee comptime.py's SizeOfInstr handling
                # relies on), so its size is already fully known here -
                # no runtime computation needed.
                ll_typ = asserts.checked_cast(self._ll_mod_items.get(instr.sized_typ), ll.Type)
                return ll.Constant(self._ll_mod_items.get(typs.USIZE), self._size(ll_typ))
            case ir_values.PtrCastInstr():
                return ctx.ll_builder.bitcast(  # type: ignore
                    ctx.ll_values.get(instr.operand), self._ll_mod_items.get(instr.target_typ)
                )
            case ir_values.PtrMutRelaxInstr():
                return asserts.checked_cast(ctx.ll_values.get(instr.operand), ll.Value)
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
            case ir_values.UnionTagInstr():
                return ctx.ll_builder.extract_value(ctx.ll_values.get(instr.operand), 0)
            case ir_values.UnionMakeInstr():
                return self._compile_union_make(instr, ctx)
            case ir_values.UnionPayloadInstr():
                return self._compile_union_payload(instr, ctx)
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

    def _union_tag_constant(self, union_typ: typs.UnionTyp, variant_index: int) -> ll.Constant:
        return ll.Constant(self._ll_mod_items.get(union_typ.tag_typ), variant_index)

    def _union_payload_ptr(
        self,
        storage: ll.Value,
        union_typ: typs.UnionTyp,
        variant_index: int,
        ctx: Compiler._FnBuilderContext,
    ) -> ll.Value:
        """A pointer to ``storage``'s payload, viewed as this variant's own struct."""
        payload_ll_typ = ll_typs.union_payload_ll_typ(
            self.ll_mod.context, union_typ.variant_at(variant_index)
        )
        field_ptr = ctx.ll_builder.gep(
            storage, [ll.Constant(ll.IntType(32), 0), ll.Constant(ll.IntType(32), 1)]
        )
        return ctx.ll_builder.bitcast(  # type: ignore
            field_ptr, ll.PointerType(payload_ll_typ)
        )

    def _compile_union_make(
        self, instr: ir_values.UnionMakeInstr, ctx: Compiler._FnBuilderContext
    ) -> ll.Value:
        union_ll_typ = self._ll_mod_items.get(instr.union_typ)
        tag = self._union_tag_constant(instr.union_typ, instr.variant_index)
        if not instr.values:
            # Nothing to store, so the whole value is the tag in an
            # otherwise undefined union.
            undef = ll.Constant(union_ll_typ, ll.Undefined)
            return ctx.ll_builder.insert_value(undef, tag, 0)

        payload_ll_typ = ll_typs.union_payload_ll_typ(
            self.ll_mod.context, instr.union_typ.variant_at(instr.variant_index)
        )
        payload: ll.Value = ll.Constant(payload_ll_typ, ll.Undefined)
        for field_index, value in enumerate(instr.values):
            payload = ctx.ll_builder.insert_value(payload, ctx.ll_values.get(value), field_index)

        # The payload is built in registers, but reaching the union from it
        # is a memory round trip: the storage field is [K x iA] and there
        # is no value-level cast between two aggregate types, so the store
        # and load are what reinterpret one as the other. SROA removes the
        # traffic again.
        storage = ctx.ll_builder.alloca(union_ll_typ)
        ctx.ll_builder.store(
            tag,
            ctx.ll_builder.gep(
                storage, [ll.Constant(ll.IntType(32), 0), ll.Constant(ll.IntType(32), 0)]
            ),
        )
        ctx.ll_builder.store(
            payload,
            self._union_payload_ptr(storage, instr.union_typ, instr.variant_index, ctx),
        )
        return ctx.ll_builder.load(storage)

    def _compile_union_payload(
        self, instr: ir_values.UnionPayloadInstr, ctx: Compiler._FnBuilderContext
    ) -> ll.Value:
        union_typ = asserts.checked_cast(instr.operand.typ, typs.UnionTyp)
        storage = ctx.ll_builder.alloca(self._ll_mod_items.get(union_typ))
        ctx.ll_builder.store(ctx.ll_values.get(instr.operand), storage)
        payload_ptr = self._union_payload_ptr(storage, union_typ, instr.variant_index, ctx)
        field_ptr = ctx.ll_builder.gep(
            payload_ptr,
            [ll.Constant(ll.IntType(32), 0), ll.Constant(ll.IntType(32), instr.field_index)],
        )
        return ctx.ll_builder.load(field_ptr)

    def _target_layout_constant(self, value: ir_values.ComptimeValue) -> ll.Value:
        """Return ``value`` as a constant of a type the target's layout gives it.

        A union cannot be a constant of its own LLVM type: that type holds
        the payload as ``[K x iA]``, and a payload of pointers or mixed
        fields has no expression as ``iA`` integers. Such a value gets a
        padded stand-in reproducing the layout the running program uses,
        and so does every aggregate containing one, because an aggregate
        constant's element types are fixed by the aggregate's own type and
        LLVM has no cast between one aggregate type and another.

        The constant's own type is the one ``_target_layout_typ`` derives
        for the same value, and always has the value's own ABI size.
        """
        if not _contains_union(value.typ):
            # Nothing forces a stand-in, so this is the ordinary constant,
            # built the one way every other constant is.
            return asserts.checked_cast(self._ll_mod_items.get(value), ll.Value)
        laid_out = self._target_layout_constant_inner(value)
        original = asserts.checked_cast(self._ll_mod_items.get(value.typ), ll.Type)
        asserts.assert_eq(self._size(_ll_typ_of(laid_out)), self._size(original))
        return laid_out

    def _target_layout_typ(self, value: ir_values.ComptimeValue) -> ll.Type:
        """The type ``_target_layout_constant`` gives ``value``, without building it.

        The two agree, but only this one can run during the declare pass.
        A type follows from the value's shape - its types, and which
        variant each union holds - while the constant needs its contents,
        resolving each pointer to the global it names. A global's LLVM
        type is fixed when it is constructed, and at that point the globals
        a constant would reach may be declared later, may hold a function
        not yet declared, or may be another module's private business.
        """
        if not _contains_union(value.typ):
            return asserts.checked_cast(self._ll_mod_items.get(value.typ), ll.Type)
        return self._target_layout_typ_inner(value)

    def _target_layout_constant_inner(self, value: ir_values.ComptimeValue) -> ll.Value:
        match value:
            case ir_values.ComptimeUnion():
                layout = self._union_layout(value)
                consts: list[ll.Value] = [self._union_tag_constant(value.typ, value.variant_index)]
                payload_ll_typ = self._union_payload_ll_typ(value)
                if payload_ll_typ is not None:
                    consts.append(self._target_layout_fields(value.payload, payload_ll_typ))
                return layout.with_typs([_ll_typ_of(const) for const in consts]).constant(consts)
            case ir_values.ComptimeStruct():
                return self._target_layout_fields(
                    [value.fields[name] for name in value.typ.fields],
                    asserts.checked_cast(
                        self._ll_mod_items.get(value.typ), ll.IdentifiedStructType
                    ),
                )
            case ir_values.ComptimeArray():
                array_ll_typ = asserts.checked_cast(self._ll_mod_items.get(value.typ), ll.ArrayType)
                # Elements holding different variants have differing padded
                # types, so this cannot stay an ArrayType; each is placed
                # at its original stride instead.
                elements = [self._target_layout_constant(elt) for elt in value.elements]
                return (
                    ll_layout.Layout.of_typ(array_ll_typ, self.ll_mod.context)
                    .with_typs([_ll_typ_of(element) for element in elements])
                    .constant(elements)
                )
            case _:
                # The caller only gets here for a value whose type holds a
                # union, and the three cases above are the only comptime
                # values such a type can have. An undef could in principle
                # have any type at all, but none can be written: every
                # literal must supply all of its parts, and every `let` an
                # initializer.
                raise AssertionError(f"{value} has a union in its type but no parts to lay out")

    def _target_layout_typ_inner(self, value: ir_values.ComptimeValue) -> ll.Type:
        match value:
            case ir_values.ComptimeUnion():
                layout = self._union_layout(value)
                part_typs = [layout.parts[0].typ]
                payload_ll_typ = self._union_payload_ll_typ(value)
                if payload_ll_typ is not None:
                    part_typs.append(self._target_layout_fields_typ(value.payload, payload_ll_typ))
                return layout.with_typs(part_typs).typ()
            case ir_values.ComptimeStruct():
                return self._target_layout_fields_typ(
                    [value.fields[name] for name in value.typ.fields],
                    asserts.checked_cast(
                        self._ll_mod_items.get(value.typ), ll.IdentifiedStructType
                    ),
                )
            case ir_values.ComptimeArray():
                array_ll_typ = asserts.checked_cast(self._ll_mod_items.get(value.typ), ll.ArrayType)
                # Every element has the array's element type, but each
                # holds its own variant, so their padded types can still
                # differ from one another.
                element_typs = [self._target_layout_typ(elt) for elt in value.elements]
                return (
                    ll_layout.Layout.of_typ(array_ll_typ, self.ll_mod.context)
                    .with_typs(element_typs)
                    .typ()
                )
            case _:
                # As in _target_layout_constant_inner: only an aggregate
                # or a union itself has a type holding a union, since no
                # source construct can leave a value undefined.
                raise AssertionError(f"{value} has a union in its type but no parts to lay out")

    def _union_payload_ll_typ(
        self, value: ir_values.ComptimeUnion
    ) -> Optional[ll.LiteralStructType]:
        """The struct ``value``'s payload is read back through, if it has one.

        The variant's ordinary unpacked struct, so a constant has to
        reproduce its field offsets rather than invent its own.
        """
        if not value.payload:
            return None
        return ll_typs.union_payload_ll_typ(
            self.ll_mod.context, value.typ.variant_at(value.variant_index)
        )

    def _union_layout(self, value: ir_values.ComptimeUnion) -> ll_layout.Layout:
        """Where a union value's tag sits, and its payload when it has one.

        A union's LLVM type is ``{tag, storage}``, so that type's own
        layout already places both. All this narrows is the storage
        field, to the payload the value actually holds - keeping the
        offset storage gives it, which is aligned for the widest of every
        variant's payloads rather than just this one's.
        """
        union_ll_typ = asserts.checked_cast(self._ll_mod_items.get(value.typ), ll.Type)
        tag_ll_typ = asserts.checked_cast(self._ll_mod_items.get(value.typ.tag_typ), ll.Type)
        return ll_layout.Layout.of_typ(union_ll_typ, self.ll_mod.context).with_typs(
            (tag_ll_typ, self._union_payload_ll_typ(value))
        )

    def _target_layout_fields(
        self,
        values: Sequence[ir_values.ComptimeValue],
        original: ll.LiteralStructType | ll.IdentifiedStructType,
    ) -> ll.Value:
        """Lay ``values`` out as ``original``'s fields, at ``original``'s offsets."""
        fields = [self._target_layout_constant(value) for value in values]
        return (
            ll_layout.Layout.of_typ(original, self.ll_mod.context)
            .with_typs([_ll_typ_of(field) for field in fields])
            .constant(fields)
        )

    def _target_layout_fields_typ(
        self,
        values: Sequence[ir_values.ComptimeValue],
        original: ll.LiteralStructType | ll.IdentifiedStructType,
    ) -> ll.Type:
        field_typs = [self._target_layout_typ(value) for value in values]
        return ll_layout.Layout.of_typ(original, self.ll_mod.context).with_typs(field_typs).typ()

    def _size(self, ll_typ: ll.Type) -> int:
        return ll_layout.abi_size(ll_typ, self.ll_mod.context)

    def _align(self, ll_typ: ll.Type) -> int:
        return ll_layout.abi_align(ll_typ, self.ll_mod.context)

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
                assert isinstance(base, ll.GlobalValue | ll.Constant)
                return base.gep([zero, ll_index])
            case ir_values.ComptimeUnion():
                # Only _laid_out_constant can build one, since a union
                # constant needs a type of its own.
                raise AssertionError("a union constant needs its own laid-out LLVM type")
            case _:
                # FnRefs are mapped during function declaration, before any
                # initializer or body can request one, so they never reach this
                # fallback. Of the remaining subclasses, a NeverValue can only
                # exist in an already-terminated block, and a ComptimeAlloc
                # cannot escape the interpreter because it is temporary.
                raise AssertionError(f"unhandled comptime value {value}")
