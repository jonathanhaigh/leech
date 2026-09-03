# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Scope and name resolution for variables, types and modules."""

import collections
import enum
from typing import Final, Optional, cast

from leech import (
    asserts,
    ast,
    compilation,
    errors,
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

type Var = (
    ir_values.Value[typs.PtrTyp]
    | ir_module.FnSymbol
    | ast.Param
    | ast.Receiver
    | ast.LetStmt
    | ast.BindingPattern
    | typs.ValueParamTyp
)
"""A value, function symbol, local declaration-site AST node, or value parameter.

Every bound ``ir_values.Value`` is ``PtrTyp``-typed; this isn't
checked, since binding is a hot path.
"""

type PathScope = Env | ir_module.Mod | typs.StructTyp | typs.EnumTyp
"""A scope that may qualify another path segment."""

type PathTarget = (
    typs.Typ
    | ir_module.Mod
    | ir_traits.Trait
    | Var
    | ir_traits.ImplFnSelection
    | ir_values.ComptimeEnum
)
"""A fully applied item produced by path resolution."""

type PathLookup = PathTarget | typs.GenericTypTemplate
"""An item produced by lookup before its segment's comptime arguments are applied."""


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

    items: Final[collections.ChainMap[tuple[Env.Namespace, str], Container | Var]]
    #: Explicit binding spans in this scope, parallel to ``items.maps[0]``.
    _spans: Final[dict[tuple[Env.Namespace, str], src.SrcSpan]]
    #: Program-wide trait implementations shared by every scope.
    impl_registry: Final[ir_traits.ImplRegistry]
    #: Compilation-wide lazy requests and active semantic computations.
    ctx: Final[compilation.Ctx]
    #: The prelude's unshadowable panic function; absent only while building the prelude.
    panic_ref: Final[Optional[ir_module.FnRef]]

    def __init__(
        self,
        ctx: compilation.Ctx,
        impl_registry: ir_traits.ImplRegistry,
        panic_ref: Optional[ir_module.FnRef],
        parent: Optional[Env] = None,
    ) -> None:
        assert impl_registry.ctx is ctx
        self.items = collections.ChainMap() if parent is None else parent.items.new_child()
        self.ctx = ctx
        self.impl_registry = impl_registry
        self.panic_ref = panic_ref
        self._spans = {}

    def new_child(self) -> Env:
        """Create a child scope inheriting this scope's bindings."""
        return Env(self.ctx, self.impl_registry, self.panic_ref, self)

    def get(self, ns: Env.Namespace, name: str) -> Optional[Container | Var]:
        """Look up ``name`` outward through ``ns``, creating integer types on demand."""
        key = (ns, name)
        try:
            return self.items[key]
        except KeyError:
            pass

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
        if key in self.items.maps[0]:
            existing = self.items[key]
            if span is None:
                span = ast.opt_span(item)
            existing_span = opt_util.opt_or_default(self._spans.get(key), ast.opt_span(existing))
            raise errors.DuplicateItemDefnError(ns.item_kind(), name, span, existing_span)
        self.items[key] = item
        if span is not None:
            self._spans[key] = span

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
        if isinstance(value, (typs.StructTypTemplate, typs.StructTyp, typs.EnumTyp)):
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
                        raise errors.PrivateItemAccessError(
                            item_kind, ident.name, ident.span, defn_span
                        )
            case typs.StructTyp():
                # Only associated functions are reachable by path:
                # `SomeStruct::x` is not a way to name a field.
                res = (
                    self.impl_registry.lookup_assoc_fn(scope, ident.name)
                    if ns == Env.Namespace.VARS
                    else None
                )
                selected_fn = opt_util.opt_map(res, lambda selection: selection.fn)
                # Private associated functions are invisible outside the
                # struct's own module, same as private Mod items above.
                if selected_fn is not None and not selected_fn.is_accessible_from(ident.span.file):
                    raise errors.PrivateItemAccessError(
                        "function", ident.name, ident.span, selected_fn.span
                    )
            case typs.EnumTyp():
                # An enum's variants are reachable by path, e.g.
                # `SomeEnum::A`; there's nothing else to reach in the
                # CONTAINERS namespace. A variant's visibility follows its
                # enum's own - there's no separate per-variant access
                # specifier - so no extra check is needed here. Unlike
                # every other VARS-namespace binding, this isn't a place
                # (a variant has no address) - it's a bare value, handled
                # like a function-reference value (see
                # `TypCheck._check_var_expr`/`CfgBuilder._build_var_expr`).
                res = (
                    ir_values.ComptimeEnum(scope, scope.variants[ident.name], None)
                    if ns == Env.Namespace.VARS and ident.name in scope.variants
                    else None
                )

        if res is None:
            raise errors.ItemNotFoundError(ns.item_kind(), ident.name, ident.span)
        return res

    def _apply_path_seg(self, item: PathLookup, seg: ast.PathSeg) -> PathLookup:
        if isinstance(item, typs.GenericTypTemplate):
            if seg.comptime_args:
                return typs.apply_generic_typ(item, seg.comptime_args, self, seg.span)
            return item

        if not seg.comptime_args:
            return item
        if isinstance(item, (ir_traits.Trait, ir_module.FnSymbol)) and item.comptime_params:
            return item
        raise errors.ComptimeArgsOnNonGenericItemError(seg.ident.name, seg.span)

    def _resolve_path_seg(
        self,
        ns: Env.Namespace,
        scope: PathScope,
        seg: ast.PathSeg,
    ) -> PathLookup:
        item = self._lookup_path_seg(ns, scope, seg.ident)
        return self._apply_path_seg(item, seg)

    @staticmethod
    def _path_target_kind(target: PathTarget) -> str:
        match target:
            case typs.TypParamTyp():
                return "type parameter"
            case typs.ValueParamTyp() | typs.ComptimeValueTyp():
                return "value"
            case typs.Typ():
                return "type"
            case ir_traits.Trait():
                return "trait"
            case ir_module.FnSymbol() | ir_traits.ImplFnSelection():
                return "function"
            case ir_module.Mod():
                return "module"
            case (
                ir_values.Value()
                | ast.Param()
                | ast.Receiver()
                | ast.LetStmt()
                | ast.BindingPattern()
            ):
                return "variable"

    @staticmethod
    def _require_path_scope(target: PathLookup, seg: ast.PathSeg) -> PathScope:
        match target:
            case ir_module.Mod() | typs.StructTyp() | typs.EnumTyp():
                return target
            case typs.GenericTypTemplate():
                raise errors.MissingComptimeArgsError(target.name, seg.span)
            case (
                typs.Typ()
                | ir_traits.Trait()
                | ir_module.FnSymbol()
                | ir_traits.ImplFnSelection()
                | ir_values.Value()
                | ast.Param()
                | ast.Receiver()
                | ast.LetStmt()
                | ast.BindingPattern()
            ):
                raise errors.ItemCannotQualifyPathError(
                    Env._path_target_kind(target),
                    target.name if isinstance(target, typs.Typ) else seg.ident.name,
                    seg.span,
                )

    def resolve_typ(self, path: ast.Path) -> typs.TypKind:
        """Resolve a qualified path whose final segment must be a type."""
        item = self.resolve_path(Env.Namespace.CONTAINERS, path)
        if isinstance(item, ir_module.Mod):
            raise errors.ModUsedAsTypError(item.name, path.segs[-1].span)
        if isinstance(item, ir_traits.Trait):
            raise errors.TraitUsedAsTypError(item.name, path.segs[-1].span)
        if isinstance(item, (typs.ValueParamTyp, typs.ComptimeValueTyp)):
            raise errors.ValueUsedAsTypError(item.name, path.segs[-1].span)
        if isinstance(item, typs.GenericTypTemplate):
            raise errors.MissingComptimeArgsError(item.name, path.segs[-1].span)
        assert isinstance(item, typs.Typ)
        return cast(typs.TypKind, item)

    def resolve_path(self, ns: Env.Namespace, path: ast.Path) -> PathLookup:
        """Resolve container path segments, then look up the final segment in ``ns``."""
        asserts.assert_ge(len(path.segs), 1)

        scope: PathScope = self
        for seg in path.segs[:-1]:
            target = self._resolve_path_seg(Env.Namespace.CONTAINERS, scope, seg)
            scope = self._require_path_scope(target, seg)

        return self._resolve_path_seg(ns, scope, path.segs[-1])

    def resolve_var(self, path: ast.Path) -> resolve.VarTarget:
        """Resolve a variable path and verify its namespace's result invariant."""
        target = self.resolve_path(Env.Namespace.VARS, path)
        if isinstance(
            target,
            (
                ir_module.ModVar,
                ir_module.FnSymbol,
                ir_traits.ImplFnSelection,
                ir_values.ComptimeEnum,
                typs.ValueParamTyp,
                ast.Param,
                ast.Receiver,
                ast.LetStmt,
                ast.BindingPattern,
            ),
        ):
            return target
        raise AssertionError(f"invalid variable path target: {target!r}")
