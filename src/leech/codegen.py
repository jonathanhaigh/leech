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

from leech import asserts, ir_module, ir_traits, ir_values, naming, signage, typs

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


def _set_linkage(ll_global: ll.GlobalVariable | ll.Function, access: ir_module.Access) -> None:
    """Set an LLVM global's linkage to match a Leech item's access.

    :param ll_global: The LLVM global variable or function to set linkage
        on.
    :param access: The Leech item's access.
    """
    if access == ir_module.PRIVATE:
        ll_global.linkage = "private"
    else:
        asserts.assert_eq(access, ir_module.PUBLIC)


class Compiler:
    """Lowers a single :class:`~leech.ir_module.Mod` to an LLVM module.

    :param mod: The IR module to compile.
    """

    _mod: Final[ir_module.Mod]
    ll_mod: Final[ll.Module]
    _ll_mod_items: Final[Compiler._LLItems]
    _tmp_name: Final[naming.VarNamer]

    class _LLItems:
        """A cache mapping Leech IR values and types to their LLVM counterparts.

        Looking up an item that hasn't been compiled yet compiles and
        caches it on demand. Like :class:`~leech.ir_env.Env`, instances form
        a parent/child chain (see :meth:`new_child`), used to give each
        function body its own scope for its instructions' LLVM values
        while still sharing the enclosing module-level cache.

        :param compiler: The compiler to dispatch on-demand compilation
            to.
        :param values: The initial mapping to wrap, or ``None`` to start
            empty.
        """

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
            """Create a new cache nested inside this one.

            :return: A fresh cache that inherits this cache's entries.
            """
            return Compiler._LLItems(self._compiler, self._items.new_child())

        def __contains__(self, item: ir_values.Value | typs.Typ) -> bool:
            return item in self._items

        def get(self, item: ir_values.Value | typs.Typ) -> ll.Value | ll.Type:
            """Get the LLVM value or type for ``item``, compiling it if needed.

            :param item: The Leech IR value or type to look up.
            :return: The corresponding LLVM value or type.
            """
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
            """Record the LLVM value or type to use for ``item``.

            :param item: The Leech IR value or type.
            :param ll_item: The LLVM value or type to associate with
                ``item``.
            """
            self._items[item] = ll_item

    @dataclasses.dataclass(frozen=True)
    class _FnBuilderContext:
        ll_builder: ll.IRBuilder
        ll_values: Compiler._LLItems
        ll_bbs: collections.ChainMap[ir_values.BasicBlock, ll.Block]

    def __init__(self, mod: ir_module.Mod) -> None:
        self._mod = mod
        self.ll_mod = ll.Module(context=ll.Context())
        self._ll_mod_items = Compiler._LLItems(self)
        self._tmp_name = naming.VarNamer()

    def compile(self) -> None:
        """Compile the module passed to the constructor into :attr:`ll_mod`.

        Runs in phases over every module in the program - not just the
        one being compiled - taken from the loader, which has already
        deduplicated modules reached by more than one import path.
        Declaring everything up front means forward references between
        items resolve regardless of declaration order, and driving the
        declare phases off the loader rather than recursing through
        imports means they don't depend on *import* order either.

        Types are declared before functions and variables because a
        signature can name a struct from a module loaded later than the
        one declaring the signature.

        Only the compiled module's own function bodies and variable
        initializers are compiled; items belonging to imported modules
        are left as declarations, to be resolved against those modules'
        own separately-compiled output at link time. Struct *bodies* are
        the exception - the compiled module needs imported structs'
        layouts to index into them.

        A generic function instance is different again: it has no
        ``ModItem`` of its own anywhere (see
        :class:`~leech.ir_module.GenericFn`) - a call to one only creates
        it, on demand, while the compiled module's own bodies are being
        lowered. So every instance any of those calls could possibly
        request is discovered first, by lowering those bodies ahead of
        the compiled-into-LLVM-IR pass below, purely to find them, and
        declared, alongside everything else, before any body - an
        ordinary function's or an instance's own - is compiled into
        LLVM IR and might need to call it.

        A generic struct's own declaration - a ``StructTyp`` with type
        parameters instead of real field types - is never declared or
        compiled at all; only its instantiations are, found and lowered
        the same on-demand way as a generic function instance, and for
        the same reason: nothing else names them until something asks
        for one.
        """
        self.ll_mod.triple = "x86_64-linux-gnu"

        for item in self._program_items():
            if isinstance(item.value, typs.StructTyp) and not item.value.is_generic_template:
                self._declare_mod_item(item)

        for item in self._program_items():
            if not isinstance(item.value, typs.StructTyp):
                self._declare_mod_item(item)

        for item in self._program_items():
            if isinstance(item.value, typs.StructTyp) and not item.value.is_generic_template:
                self._compile_mod_struct(item, item.value)

        # A generic struct's own fields are never lowered - only an
        # instantiation's are, below - but they're still validated here,
        # against the struct's own (opaque) type parameters, the same as
        # every other declared struct's: an infinite-size or
        # runaway-instantiation-depth struct is rejected whether or not
        # anything in the program ever instantiates it.
        for item in self._program_items():
            if isinstance(item.value, typs.StructTyp) and item.value.is_generic_template:
                _ = item.value.fields

        instances = self._discover_fn_instances()
        struct_instances = self._discover_struct_instances()
        for struct_inst in struct_instances:
            self._declare_struct_instance(struct_inst)
        for struct_inst in struct_instances:
            self._compile_struct_instance(struct_inst)

        for inst in instances:
            self._declare_fn_instance(inst)

        for item in self._mod.items:
            self._compile_mod_item(item)

        for inst in instances:
            self._compile_fn_instance(inst)

    def _program_items(self) -> Iterator[ir_module.ModItem]:
        """Every item of every module in the program that needs an LLVM symbol.

        Imported modules contribute only their public items, since
        nothing outside them can refer to the rest. Items that are
        themselves imported modules are skipped: this walk already covers
        every module, so recursing into them would be redundant. Item
        kinds with nothing to declare or compile - a generic function
        (only its instances have an LLVM symbol, see
        :meth:`_discover_fn_instances`) or a trait (a declaration, not a
        value or type with anything to lower) - aren't filtered out here;
        :meth:`_declare_mod_item` and :meth:`_compile_mod_item` are the
        single place that decides what each item kind needs.

        :return: The items, grouped by module in load order.
        """
        for mod in self._mod.loader.mods:
            for item in mod.items:
                if isinstance(item.value, ir_module.Mod):
                    continue
                if mod is self._mod or item.access == ir_module.PUBLIC:
                    yield item

    def _fn_templates(self) -> Iterator[ir_module.FnTemplate]:
        """Every generic function or compiler-intrinsic builtin in the
        program, whose :attr:`~leech.ir_module.FnTemplate.instances` might
        need discovering (see :meth:`_discover_fn_instances`)."""
        for mod in self._mod.loader.mods:
            for item in mod.items:
                if isinstance(item.value, ir_module.GenericFn):
                    yield item.value.fn
        yield from self._mod.loader.builtins

    def _discover_fn_instances(self) -> list[ir_module.FnInstance]:
        """Find every generic function instance a call anywhere in the
        program could request.

        An instance is created only as a side effect of lowering (see
        :attr:`~leech.ir_module.Fn.cfg`) a body that calls one - and
        lowering one instance's own body can request further instances of
        the same or another generic function (a nested or recursive
        generic call). So this forces every one of :attr:`_mod`'s own
        functions to be lowered first, seeding the search, then keeps
        lowering whatever instances that turns up - regardless of which
        module's generic function they instantiate - until a full sweep
        turns up nothing new.

        This is pure IR-building, with no LLVM involved: an instance's
        own :attr:`~leech.ir_module.Fn.cfg` is looked up (and, on first
        use, built) here purely to complete the search, well before
        :meth:`_compile_fn_instance` compiles it into LLVM IR.

        A compiler-intrinsic builtin (see
        :class:`~leech.ir_module.GenericBuiltinFn`) is never a
        :class:`~leech.ir_module.ModItem` - it's bound directly into every
        module's ``builtin_env``, not through the ordinary
        ``ast.defns``-driven path - so :meth:`_fn_templates` yields it
        alongside every real generic function, and the sweep below doesn't
        need to tell the two apart.

        :return: Every instance discovered, in discovery order.
        """
        instances: list[ir_module.FnInstance] = []
        seen: set[ir_module.FnInstance] = set()

        def newly_requested() -> list[ir_module.FnInstance]:
            new = []
            for template in self._fn_templates():
                for inst in template.instances:
                    if inst not in seen:
                        seen.add(inst)
                        new.append(inst)
            return new

        for item in self._mod.items:
            if isinstance(item.value, (ir_module.Fn, ir_module.ModVar)):
                _ = item.value.cfg

        pending = newly_requested()
        while pending:
            instances.extend(pending)
            for inst in pending:
                _ = inst.cfg
            pending = newly_requested()

        return instances

    def _discover_struct_instances(self) -> list[typs.StructTyp]:
        """Find every generic struct instantiation reachable from the program.

        Mirrors :meth:`_discover_fn_instances`: an instantiation is
        created on demand wherever its type is named - a struct literal,
        a ``let``'s declared type, a function signature, another struct's
        own field, ... - and one instantiation's own fields can themselves
        name further instantiations, of the same or another generic
        struct, so this iterates to a fixpoint the same way.

        Run *after* :meth:`_discover_fn_instances`, which has already
        forced every reachable function body to lower - and so already
        requested (see :meth:`~leech.typs.StructTyp.instance`) every
        instantiation any of them directly names. This only has to chase
        the ones reachable purely through struct fields from there, which
        involves no lowering, just resolving :attr:`~leech.typs.StructTyp.fields`.

        Filters to :meth:`~leech.typs.StructTyp.is_concrete` instantiations:
        type-checking a generic function or ``impl`` block resolves its own
        type parameters opaquely, which also produces an entry in
        :attr:`~leech.typs.StructTyp.instances` - one that names no real,
        lowerable type, and is never reachable through anything that
        actually gets lowered.

        :return: Every instantiation discovered, in discovery order.
        """
        instances: list[typs.StructTyp] = []
        seen: set[typs.StructTyp] = set()

        def newly_requested() -> list[typs.StructTyp]:
            new = []
            for mod in self._mod.loader.mods:
                for item in mod.items:
                    if isinstance(item.value, typs.StructTyp) and item.value.is_generic_template:
                        for inst in item.value.instances:
                            if inst.is_concrete() and inst not in seen:
                                seen.add(inst)
                                new.append(inst)
            return new

        pending = newly_requested()
        while pending:
            instances.extend(pending)
            for inst in pending:
                _ = inst.fields
            pending = newly_requested()

        return instances

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
                return ll.ArrayType(self._ll_mod_items.get(typ.element_typ), typ.length)
            case typs.VoidTyp():
                return ll.VoidType()
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
            case ir_module.FnSpec():
                self._declare_mod_fn(item, item.value)
            case typs.StructTyp():
                ll_item = self.ll_mod.context.get_identified_type(item.qualified_name)
                self._ll_mod_items.set(item.value, ll_item)
            case ir_module.GenericFn():
                # No LLVM symbol of its own - only its instances do (see
                # _discover_fn_instances), declared separately.
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
            case ir_module.Fn():
                return self._compile_mod_fn(item, item.value)
            case ir_module.FnDecl():
                return None
            case typs.StructTyp():
                # Already compiled, along with every other module's, by
                # the struct-body phase of compile().
                return None
            case ir_module.Mod():
                # An import contributes no symbols of its own; the
                # imported module's items are handled by compile()'s walk
                # over the whole program.
                return None
            case ir_module.GenericFn():
                # No LLVM symbol of its own to compile - only its
                # instances do (compiled separately by compile()).
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

    def _declare_mod_fn(self, item: ir_module.ModItem, fn: ir_module.FnSpec) -> ll.Value:
        ll_fn = ll.Function(self.ll_mod, self._ll_mod_items.get(fn.fn_typ), item.qualified_name)
        self._ll_mod_items.set(fn, ll_fn)
        # TODO: linkage for FnDecls?
        _set_linkage(ll_fn, item.access)
        return ll_fn

    def _compile_mod_fn(self, _item: ir_module.ModItem, fn: ir_module.Fn) -> None:
        self._compile_fn_body(fn, fn.params, fn.cfg)

    def _declare_fn_instance(self, inst: ir_module.FnInstance) -> ll.Value:
        ll_fn = ll.Function(self.ll_mod, self._ll_mod_items.get(inst.fn_typ), inst.qualified_name)
        self._ll_mod_items.set(inst, ll_fn)
        # An instance emitted here because *this* module calls it may be
        # emitted again, identically, wherever else it's called (another
        # module, or this one compiled as its own root) - linkonce_odr
        # tells the linker every such copy is interchangeable, so it can
        # keep one and discard the rest instead of rejecting the file as
        # a duplicate definition.
        ll_fn.linkage = "linkonce_odr"
        return ll_fn

    def _compile_fn_instance(self, inst: ir_module.FnInstance) -> None:
        self._compile_fn_body(inst, inst.params, inst.cfg)

    def _compile_fn_body(
        self,
        fn_value: ir_values.Value,
        params: tuple[ir_values.Param, ...],
        cfg: ir_values.Cfg,
    ) -> None:
        """Compile a function's or generic function instance's lowered
        body into its already-declared LLVM function.

        :param fn_value: The ``Fn`` or ``FnInstance`` :meth:`_declare_mod_fn`
            or :meth:`_declare_fn_instance` already declared an LLVM
            function for.
        :param params: Its formal parameters, in declaration order.
        :param cfg: Its lowered control-flow graph.
        """
        ctx = Compiler._FnBuilderContext(
            ll_builder=ll.IRBuilder(),
            ll_values=self._ll_mod_items.new_child(),
            ll_bbs=collections.ChainMap(),
        )

        ll_fn = asserts.checked_cast(self._ll_mod_items.get(fn_value), ll.Function)
        for param, ll_param in zip(params, ll_fn.args, strict=True):
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
        bb_order = reversed(list(nx.dfs_postorder_nodes(cfg, cfg.entry)))
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

    def _compile_instr(self, instr: ir_values.Instr, ctx: Compiler._FnBuilderContext) -> ll.Value:
        match instr:
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
                # NeverValue and ComptimeAlloc are the only other
                # ComptimeValue subclasses: a NeverValue can only exist
                # in an already-terminated block, so nothing ever reads
                # it as an operand; a ComptimeAlloc's is_temporary() is
                # always true, so it can never escape the interpreter as
                # a value in its own right (see
                # CannotTakeAddressOfComptimeValueError).
                raise AssertionError(f"unhandled comptime value {value}")
