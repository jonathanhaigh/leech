# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Scope and name resolution for variables, types and modules."""

import collections
import contextlib
import dataclasses
import enum
from collections.abc import Iterator
from typing import Final, NoReturn, Optional, cast

from leech import (
    asserts,
    ast,
    compilation,
    diag,
    diag_kinds,
    ir_module,
    ir_traits,
    ir_values,
    opt_util,
    resolve,
    src,
    typs,
    visibility,
)

type Container = typs.Typ | typs.GenericTypTemplate | ir_module.Mod | ir_traits.Trait
"""A type, module, or trait bound in the shared container namespace."""

type NonFnVar = (
    ir_values.Value[typs.PtrTyp]
    | ast.Param
    | ast.Receiver
    | ast.LetStmt
    | ast.BindingPattern
    | typs.ValueParamTyp
)
"""A non-function binding in the variable namespace."""

type Var = NonFnVar | ir_module.FnSymbol
"""A variable-namespace binding before path application."""

type PathScope = (
    Env | ir_module.Mod | typs.StructTyp | typs.EnumTyp | typs.UnionTyp | typs.UnionTypTemplate
)
"""A scope that may qualify another path segment."""

type PathTarget = (
    typs.Typ
    | ir_module.Mod
    | ir_traits.TraitApplication
    | NonFnVar
    | ir_module.FnCandidate
    | ir_values.ComptimeEnum
    | typs.UnionVariantRef
)
"""A fully applied item produced by path resolution."""

type PathResult = PathTarget | typs.GenericTypTemplate
"""A path result, including a final unapplied generic type template."""

type PathLookup = (
    Container | Var | ir_traits.ImplFnSelection | ir_values.ComptimeEnum | typs.UnionVariantRef
)
"""An item produced by lookup before its segment's comptime arguments are applied."""

type _ContainerResult = (
    typs.Typ | typs.GenericTypTemplate | ir_module.Mod | ir_traits.TraitApplication
)


@dataclasses.dataclass
class PendingBinding:
    """A name checked for binding by ``Env.binding``, whose value is still being built."""

    value: Optional[Container | Var] = None

    def bind(self, value: Container | Var) -> None:
        assert self.value is None, "a pending binding is bound once"
        self.value = value

    def bound(self) -> Container | Var:
        return opt_util.opt_unwrap(self.value)


@dataclasses.dataclass(frozen=True)
class PoisonedName:
    """What a name is bound to when its definition was rejected with an error."""

    reported: diag.ReportProof


class Env:
    """A chained lexical scope with separate variable and container namespaces.

    Child scopes inherit the program-wide impl registry and intrinsic panic function.
    """

    class Namespace(enum.Enum):
        """Which kind of item a name is being looked up or bound in."""

        VARS = 0
        CONTAINERS = 1

        def item_kind(self) -> str:
            """A human-readable label for this namespace, for diagnostics."""
            match self:
                case Env.Namespace.VARS:
                    return "variable"
                case Env.Namespace.CONTAINERS:
                    return "type or module"

    items: Final[collections.ChainMap[tuple[Env.Namespace, str], Container | Var | PoisonedName]]
    #: Explicit binding spans in this scope, parallel to ``items.maps[0]``.
    _spans: Final[dict[tuple[Env.Namespace, str], src.SrcSpan]]
    #: The compilation this scope belongs to.
    ctx: Final[compilation.Ctx]

    def __init__(self, ctx: compilation.Ctx, parent: Optional[Env] = None) -> None:
        self.items = collections.ChainMap() if parent is None else parent.items.new_child()
        self.ctx = ctx
        self._spans = {}

    def new_child(self) -> Env:
        """Create a child scope inheriting this scope's bindings."""
        return Env(self.ctx, self)

    def get(self, ns: Env.Namespace, name: str) -> Optional[Container | Var]:
        """Look up ``name`` outward through ``ns``, creating integer types on demand.

        Looking up a poisoned name raises ``diag.ReportedError`` with the proof of the error
        that rejected its definition, so a use reports nothing more.
        """
        key = (ns, name)
        try:
            item = self.items[key]
        except KeyError:
            pass
        else:
            if isinstance(item, PoisonedName):
                raise diag.ReportedError(item.reported)
            return item

        if ns == Env.Namespace.CONTAINERS:
            typ = typs.IntTyp.from_name(name)
            if typ is not None:
                self.items.maps[-1][key] = typ
                return typ

        return None

    def add(
        self,
        ns: Env.Namespace,
        name: str,
        item: Container | Var,
        span: Optional[src.SrcSpan] = None,
    ) -> None:
        """Bind ``name`` in this scope, using ``span`` for duplicate diagnostics."""
        key = (ns, name)
        self._check_can_add(ns, name, opt_util.opt_or_default(span, ast.opt_span(item)))
        self.items[key] = item
        if span is not None:
            self._spans[key] = span

    @contextlib.contextmanager
    def binding(self, ns: Env.Namespace, name: str, span: src.SrcSpan) -> Iterator[PendingBinding]:
        """Check now that ``name`` can be bound in this scope, and bind it after the block.

        The block passes the value to the yielded ``PendingBinding``'s ``bind``. If the block
        raises, nothing is bound. The name is checked again before binding, in case it was
        bound during the block.
        """
        self._check_can_add(ns, name, span)
        pending = PendingBinding()
        yield pending
        self.add(ns, name, pending.bound(), span)

    def _check_can_add(self, ns: Env.Namespace, name: str, span: Optional[src.SrcSpan]) -> None:
        key = (ns, name)
        if key in self.items.maps[0]:
            existing_span = opt_util.opt_or_default(
                self._spans.get(key), ast.opt_span(self.items[key])
            )
            d = diag.Diag.new(
                diag_kinds.DUPLICATE_DEFINITION, span, item_kind=ns.item_kind(), name=name
            )
            self.ctx.diags.raise_error(d.with_label(diag_kinds.PREVIOUS_DEFN_HERE, existing_span))

    def poison(
        self, ns: Env.Namespace, name: str, reported: diag.ReportProof, span: src.SrcSpan
    ) -> None:
        """Bind ``name`` in this scope to a definition that was rejected for ``reported``."""
        key = (ns, name)
        assert key not in self.items.maps[0], f"{name} is already bound"
        self.items[key] = PoisonedName(reported)
        self._spans[key] = span

    def is_bound_here(self, ns: Env.Namespace, name: str) -> bool:
        return (ns, name) in self.items.maps[0]

    def add_var(
        self,
        name: str,
        var: Var,
        span: Optional[src.SrcSpan] = None,
    ) -> None:
        """Bind a variable in this scope."""
        return self.add(Env.Namespace.VARS, name, var, span)

    def add_container(self, name: str, item: Container, span: Optional[src.SrcSpan] = None) -> None:
        """Bind a type, module, or trait in this scope."""
        return self.add(Env.Namespace.CONTAINERS, name, item, span)

    @staticmethod
    def _private_item_diag_info(
        value: Container | ir_values.Value[typs.PtrTyp] | ir_module.FnSymbol,
    ) -> Optional[tuple[str, Optional[src.SrcSpan]]]:
        """Return a private item's diagnostic kind and definition span, if applicable.

        Only ever called with a ``ir_module.ModItem``'s own
        value, never a local binding - narrower than the general ``Var``.
        """
        if isinstance(value, ir_module.FnSymbol):
            return "function", value.span
        if isinstance(value, ir_module.ModVar):
            return "variable", value.span
        if isinstance(
            value,
            typs.StructTypTemplate
            | typs.StructTyp
            | typs.UnionTypTemplate
            | typs.UnionTyp
            | typs.EnumTyp,
        ):
            return "type", value.span
        if isinstance(value, ir_traits.Trait):
            return "trait", value.span
        return None

    def _lookup_path_seg(
        self,
        ns: Env.Namespace,
        scope: PathScope,
        ident: ast.Ident,
    ) -> PathLookup:
        match scope:
            case Env():
                res = scope.get(ns, ident.name)
            case ir_module.Mod():
                item = scope.get_item(ns, ident.name)
                if item is None or item.access == visibility.PUBLIC:
                    res = opt_util.opt_map(item, lambda i: i.value)
                else:
                    diag_info = Env._private_item_diag_info(item.value)
                    if diag_info is None:
                        res = None
                    else:
                        item_kind, defn_span = diag_info
                        self._raise_private_item_access(
                            item_kind, ident.name, ident.span, defn_span
                        )
            case typs.StructTyp():
                # Only associated functions are reachable by path:
                # `SomeStruct::x` is not a way to name a field.
                res = self._lookup_assoc_fn(ns, scope, ident)
            case typs.UnionTypTemplate():
                # The one place a union template behaves unlike a struct
                # template, which reports missing-comptime-argument here: a
                # variant's comptime arguments are inferable from its
                # payload or its expected type, so naming one without them
                # is how `Option::Some(x)` is meant to be written.
                res = self._lookup_union_variant(ns, scope, scope, ident)
            case typs.UnionTyp():
                res = self._lookup_union_variant(
                    ns, scope, scope.template, ident
                ) or self._lookup_assoc_fn(ns, scope, ident)
            case typs.EnumTyp():
                # Variants inherit the enum's visibility and are immediate
                # values in the variable namespace rather than places.
                res = (
                    ir_values.ComptimeEnum(scope, scope.variants[ident.name], None)
                    if ns == Env.Namespace.VARS and ident.name in scope.variants
                    else None
                )

        if res is None:
            self.ctx.diags.raise_error(
                diag_kinds.UNKNOWN_NAME, ident.span, item_kind=ns.item_kind(), name=ident.name
            )
        return res

    def _raise_private_item_access(
        self,
        item_kind: str,
        name: str,
        access_span: src.SrcSpan,
        defn_span: Optional[src.SrcSpan],
    ) -> NoReturn:
        d = diag.Diag.new(
            diag_kinds.PRIVATE_ITEM_ACCESS, access_span, item_kind=item_kind, name=name
        )
        self.ctx.diags.raise_error(d.with_label(diag_kinds.DEFINED_HERE, defn_span, name=name))

    def _lookup_assoc_fn(
        self,
        ns: Env.Namespace,
        scope: typs.Typ,
        ident: ast.Ident,
    ) -> Optional[ir_traits.ImplFnSelection]:
        if ns != Env.Namespace.VARS:
            return None
        res = self.ctx.impl_registry.lookup_assoc_fn(scope, ident.name)
        selected_fn = opt_util.opt_map(res, lambda selection: selection.fn)
        # Private associated functions are invisible outside the type's own
        # module, same as private Mod items above.
        if selected_fn is not None and not selected_fn.is_accessible_from(ident.span.file):
            self._raise_private_item_access("function", ident.name, ident.span, selected_fn.span)
        return res

    @staticmethod
    def _lookup_union_variant(
        ns: Env.Namespace,
        owner: typs.UnionTypTemplate | typs.UnionTyp,
        template: typs.UnionTypTemplate,
        ident: ast.Ident,
    ) -> Optional[typs.UnionVariantRef]:
        """Find a variant by name, which needs no visibility check of its own.

        A variant is exactly as accessible as its union, so reaching this
        scope at all has already established access.
        """
        if ns != Env.Namespace.VARS:
            return None
        variant = template.variants.get(ident.name)
        if variant is None:
            return None
        return typs.UnionVariantRef(owner, variant)

    def _apply_fn_path_seg(
        self,
        fn: ir_module.FnSymbol,
        impl_args: tuple[typs.Typ, ...],
        seg: ast.PathSeg,
    ) -> ir_module.FnCandidate:
        if not fn.comptime_params:
            if seg.comptime_args:
                self.ctx.diags.raise_error(
                    diag_kinds.UNEXPECTED_COMPTIME_ARGUMENT, seg.span, item=fn.name
                )
            explicit_fn_args: Optional[tuple[typs.Typ, ...]] = ()
        elif seg.comptime_args:
            explicit_fn_args = typs.resolve_explicit_comptime_args(
                fn.name, fn.comptime_params, seg.comptime_args, self, seg.span
            )
        else:
            explicit_fn_args = None
        return ir_module.FnCandidate(fn, impl_args, explicit_fn_args)

    def _apply_path_seg(self, item: PathLookup, seg: ast.PathSeg) -> PathResult:
        if isinstance(item, typs.GenericTypTemplate):
            if seg.comptime_args:
                return typs.apply_generic_typ(item, seg.comptime_args, self, seg.span)
            return item

        if isinstance(item, ir_traits.Trait):
            args = typs.resolve_explicit_comptime_args(
                item.name, item.comptime_params, seg.comptime_args, self, seg.span
            )
            return ir_traits.TraitApplication(item, args)

        if isinstance(item, ir_traits.ImplFnSelection):
            return self._apply_fn_path_seg(item.fn, item.impl_args, seg)

        if isinstance(item, ir_module.FnSymbol):
            impl_args: tuple[typs.Typ, ...] = ()
            if isinstance(item, ir_module.SrcFnSymbol) and item.impl is not None:
                impl_args = item.impl.comptime_params
            return self._apply_fn_path_seg(item, impl_args, seg)

        if not seg.comptime_args:
            return item
        self.ctx.diags.raise_error(
            diag_kinds.UNEXPECTED_COMPTIME_ARGUMENT, seg.span, item=seg.ident.name
        )

    def _resolve_path_seg(
        self,
        ns: Env.Namespace,
        scope: PathScope,
        seg: ast.PathSeg,
    ) -> PathResult:
        item = self._lookup_path_seg(ns, scope, seg.ident)
        return self._apply_path_seg(item, seg)

    @staticmethod
    def _path_target_kind(target: PathResult) -> str:
        match target:
            case typs.TypParamTyp():
                return "type parameter"
            case typs.ValueParamTyp() | typs.ComptimeValueTyp():
                return "value"
            case typs.GenericTypTemplate():
                return "type"
            case typs.Typ():
                return "type"
            case ir_traits.TraitApplication():
                return "trait"
            case ir_module.FnCandidate():
                return "function"
            case ir_module.Mod():
                return "module"
            case typs.UnionVariantRef():
                return "variant"
            case (
                ir_values.Value()
                | ast.Param()
                | ast.Receiver()
                | ast.LetStmt()
                | ast.BindingPattern()
            ):
                return "variable"

    def _require_path_scope(self, target: PathResult, seg: ast.PathSeg) -> PathScope:
        match target:
            case ir_module.Mod() | typs.StructTyp() | typs.EnumTyp() | typs.UnionTyp():
                return target
            case typs.UnionTypTemplate():
                # A union template qualifies a path even unapplied, so that
                # `Option::Some` can name a variant whose comptime
                # arguments are still to be inferred.
                return target
            case typs.GenericTypTemplate():
                self.ctx.diags.raise_error(
                    diag_kinds.MISSING_COMPTIME_ARGUMENT, seg.span, item=target.name
                )
            case (
                typs.Typ()
                | typs.UnionVariantRef()
                | ir_traits.TraitApplication()
                | ir_module.FnCandidate()
                | ir_values.Value()
                | ast.Param()
                | ast.Receiver()
                | ast.LetStmt()
                | ast.BindingPattern()
            ):
                name: diag.DiagArgValue = seg.ident.name
                if isinstance(target, typs.Typ):
                    name = target
                self.ctx.diags.raise_error(
                    diag_kinds.PATH_QUALIFIER_KIND_MISMATCH,
                    seg.span,
                    item_kind=Env._path_target_kind(target),
                    name=name,
                )

    def resolve_typ(self, path: ast.Path) -> typs.TypKind:
        """Resolve a qualified path whose final segment must be a type."""
        lookup, final_seg = self._lookup_final_path_seg(Env.Namespace.CONTAINERS, path)
        if isinstance(lookup, ir_traits.Trait):
            if final_seg.comptime_args and not lookup.comptime_params:
                self.ctx.diags.raise_error(
                    diag_kinds.UNEXPECTED_COMPTIME_ARGUMENT, final_seg.span, item=lookup.name
                )
            self.ctx.diags.raise_error(
                diag_kinds.TRAIT_USED_AS_TYPE, final_seg.span, trait=lookup.name
            )
        item = self._apply_path_seg(lookup, final_seg)
        if isinstance(item, ir_module.Mod):
            d = diag.Diag.new(
                diag_kinds.MODULE_USED_AS_TYPE, final_seg.span, mod=item.name
            ).with_note(diag_kinds.MOD_QUALIFIES_PATHS, mod=item.name)
            self.ctx.diags.raise_error(d)
        if isinstance(item, typs.ValueParamTyp | typs.ComptimeValueTyp):
            self.ctx.diags.raise_error(diag_kinds.VALUE_USED_AS_TYPE, final_seg.span, name=item)
        if isinstance(item, typs.GenericTypTemplate):
            self.ctx.diags.raise_error(
                diag_kinds.MISSING_COMPTIME_ARGUMENT, final_seg.span, item=item.name
            )
        assert isinstance(item, typs.Typ)
        return cast(typs.TypKind, item)

    def _lookup_final_path_seg(
        self, ns: Env.Namespace, path: ast.Path
    ) -> tuple[PathLookup, ast.PathSeg]:
        asserts.assert_ge(len(path.segs), 1)

        scope: PathScope = self
        for seg in path.segs[:-1]:
            target = self._resolve_path_seg(Env.Namespace.CONTAINERS, scope, seg)
            scope = self._require_path_scope(target, seg)

        final_seg = path.segs[-1]
        return self._lookup_path_seg(ns, scope, final_seg.ident), final_seg

    def _resolve_path(self, ns: Env.Namespace, path: ast.Path) -> PathResult:
        """Resolve container path segments, then apply the final segment in ``ns``."""
        item, final_seg = self._lookup_final_path_seg(ns, path)
        return self._apply_path_seg(item, final_seg)

    def _resolve_container(self, path: ast.Path) -> _ContainerResult:
        target = self._resolve_path(Env.Namespace.CONTAINERS, path)
        if isinstance(
            target,
            typs.Typ | typs.GenericTypTemplate | ir_module.Mod | ir_traits.TraitApplication,
        ):
            return target
        raise AssertionError(f"invalid container path target: {target!r}")

    def resolve_trait(self, path: ast.Path) -> ir_traits.TraitApplication:
        """Resolve a trait application and reject any other container kind."""
        target = self._resolve_container(path)
        if not isinstance(target, ir_traits.TraitApplication):
            self._raise_path_kind_mismatch(path, target, "trait")
        return target

    def resolve_comptime_value(self, path: ast.Path) -> typs.ValueParamTyp | typs.ComptimeValueTyp:
        """Resolve a comptime value and reject any other container kind."""
        target = self._resolve_container(path)
        if not isinstance(target, typs.ValueParamTyp | typs.ComptimeValueTyp):
            self._raise_path_kind_mismatch(path, target, "comptime value")
        return target

    def _raise_path_kind_mismatch(
        self, path: ast.Path, target: PathResult, expected_kind: str
    ) -> NoReturn:
        self.ctx.diags.raise_error(
            diag_kinds.PATH_KIND_MISMATCH,
            path.span,
            path=path.str(),
            actual_kind=self._path_target_kind(target),
            expected_kind=expected_kind,
        )

    def resolve_var(self, path: ast.Path) -> resolve.VarTarget:
        """Resolve a variable path and verify its namespace's result invariant."""
        target = self._resolve_path(Env.Namespace.VARS, path)
        if isinstance(target, ir_values.ComptimeEnum | typs.UnionVariantRef):
            return target
        if isinstance(target, ir_values.Value):
            asserts.checked_cast(target.typ, typs.PtrTyp)
            return asserts.checked_cast(target, ir_module.ModVar)
        if isinstance(
            target,
            ir_module.FnCandidate
            | typs.ValueParamTyp
            | ast.Param
            | ast.Receiver
            | ast.LetStmt
            | ast.BindingPattern,
        ):
            return target
        raise AssertionError(f"invalid variable path target: {target!r}")
