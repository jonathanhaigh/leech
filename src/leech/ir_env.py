"""Scope and name resolution for variables, types and modules."""

import enum
import re
from collections import ChainMap
from typing import Any

from leech import ast, ir_module, ir_traits, ir_values, typs
from leech.asserts import assert_ge, checked_cast
from leech.errors import (
    DuplicateItemDefnError,
    ItemNotFoundError,
    ModUsedAsTypError,
    PrivateItemAccessError,
    TraitUsedAsTypError,
)
from leech.opt_util import opt_map, opt_or_else
from leech.src import SrcSpan

type Container = typs.Typ | ir_module.Mod | ir_traits.Trait
"""Anything bindable in the :attr:`Env.Namespace.CONTAINERS` namespace.

A type, an imported module, or a trait. None of the three can share a
name with another: a module isn't a type but shares this namespace with
one so a module and a type can't collide, and a trait shares it too (see
:meth:`Env.resolve_typ`) so that ``dyn Trait`` remains addable later
without a namespace change. All three can hold associated items, which is
what the namespace's name refers to.
"""

type Var = ir_values.Value[typs.PtrTyp] | ir_module.GenericFn
"""Anything bindable in the :attr:`Env.Namespace.VARS` namespace.

A value, or a :class:`~leech.ir_module.GenericFn` standing in for one
until it's applied to type arguments (see that class's docstring).
"""


class Env:
    """A lexical scope mapping names to variables, types and modules.

    Scopes are chained: a child scope (see :meth:`new_child`) sees its own
    bindings plus everything visible in its ancestors, and shadows
    ancestor bindings of the same name. Names are looked up in separate
    namespaces (see :class:`Namespace`), so a variable and a type may
    share a name without conflict - but a type and a module may not,
    since those share one namespace.

    :param parent: The enclosing scope, or ``None`` to create a fresh,
        top-level scope.
    :param impl_registry: The program-wide trait impl registry (see
        :class:`~leech.ir_traits.ImplRegistry`) to expose as
        :attr:`impl_registry`. Only meaningful (and only ever passed) at a
        top-level scope - a child inherits its parent's, always. Defaults
        to a fresh, empty registry so tests and other isolated callers
        that construct a bare top-level ``Env`` don't have to know traits
        exist; real compilation always passes the loader's own (see
        :meth:`~leech.ir_module.Mod.__init__`).
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

    items: ChainMap[tuple[Env.Namespace, str], Container | Var]
    #: Where a name was bound, for bindings whose item can't point at its
    #: own definition site (see :meth:`add`'s ``span`` parameter). Scoped
    #: to this Env alone, mirroring ``items.maps[0]``.
    _spans: dict[tuple[Env.Namespace, str], SrcSpan]
    #: The program-wide trait impl registry - reachable from any scope, so
    #: that type and method resolution (which thread an ``Env`` through
    #: pervasively already) don't need it passed as a second, parallel
    #: parameter everywhere. See :class:`~leech.ir_traits.ImplRegistry`.
    impl_registry: ir_traits.ImplRegistry

    def __init__(
        self,
        parent: Env | None = None,
        impl_registry: ir_traits.ImplRegistry | None = None,
    ) -> None:
        if parent is None:
            self.items = ChainMap()
            self.impl_registry = opt_or_else(impl_registry, ir_traits.ImplRegistry)
        else:
            self.items = parent.items.new_child()
            self.impl_registry = parent.impl_registry
        self._spans = {}

    def new_child(self) -> Env:
        """Create a new scope nested inside this one.

        :return: A fresh scope that inherits this scope's bindings.
        """
        return Env(self)

    def get(self, ns: Env.Namespace, name: str) -> Any:
        """Look up ``name`` in ``ns``, searching outward through parent scopes.

        Integer type names (e.g. ``i32``, ``u8``) are recognized and
        created on demand when looked up in the container namespace.

        :param ns: The namespace to search in.
        :param name: The name to look up.
        :return: The bound item, or ``None`` if ``name`` is not bound.
        """
        key = (ns, name)
        if key in self.items:
            return self.items[key]

        if ns == Env.Namespace.CONTAINERS:
            m = re.fullmatch("([iu])([1-9][0-9]*)", name)
            if m:
                signage = typs.SIGNED if m[1] == "i" else typs.UNSIGNED
                width = int(m[2])
                typ = typs.IntTyp.get_or_create(width, signage)
                self.items.maps[-1][key] = typ
                return typ

        return None

    def add(
        self,
        ns: Env.Namespace,
        name: str,
        item: Any,
        span: SrcSpan | None = None,
    ) -> None:
        """Bind ``name`` to ``item`` in ``ns``, in this scope only.

        :param ns: The namespace to bind in.
        :param name: The name to bind.
        :param item: The item to bind ``name`` to.
        :param span: Where the binding is written, for the duplicate
            diagnostic. Defaults to the span of ``item``'s own AST node,
            which is the right answer for everything except an ``import``
            (whose item is a whole :class:`~leech.ir_module.Mod`, whose AST
            node covers the entire imported file rather than the
            ``import`` statement).
        :raises DuplicateItemDefnError: If ``name`` is already bound in
            ``ns`` in this scope.
        """
        key = (ns, name)
        if key in self.items.maps[0]:
            existing = self.items[key]
            if span is None:
                span = ast.opt_span(item)
            existing_span = self._spans.get(key) or ast.opt_span(existing)
            raise DuplicateItemDefnError(ns.item_kind(), name, span, existing_span)
        self.items[key] = item
        if span is not None:
            self._spans[key] = span

    def add_var(
        self,
        name: str,
        var: Var,
        span: SrcSpan | None = None,
    ) -> None:
        """Bind a variable name in this scope.

        :param name: The name to bind.
        :param var: The variable to bind ``name`` to.
        :param span: Where the binding is written; see :meth:`add`.
        :raises DuplicateItemDefnError: If ``name`` is already bound to a
            variable in this scope.
        """
        return self.add(Env.Namespace.VARS, name, var, span)

    def add_container(self, name: str, item: Container, span: SrcSpan | None = None) -> None:
        """Bind a type or module name in this scope.

        :param name: The name to bind.
        :param item: The type or module to bind ``name`` to.
        :param span: Where the binding is written; see :meth:`add`.
        :raises DuplicateItemDefnError: If ``name`` is already bound to a
            type or module in this scope.
        """
        return self.add(Env.Namespace.CONTAINERS, name, item, span)

    @staticmethod
    def _private_item_diag_info(
        value: Container | Var,
    ) -> tuple[str, SrcSpan | None] | None:
        """A human-readable kind and definition span for a private-item
        diagnostic, if one applies.

        :param value: The module item being accessed.
        :return: A ``(kind, defn_span)`` pair, where ``kind`` is
            ``"function"``, ``"variable"``, or ``"type"``; or ``None`` if
            ``value`` has no user-facing privacy concept to report (e.g. an
            imported module, which can't be marked ``pub`` at all) - in
            that case callers should fall back to treating it as not found.
        """
        if isinstance(value, ir_module.FnSpec):
            return "function", value.span
        if isinstance(value, ir_module.GenericFn):
            return "function", value.span
        if isinstance(value, ir_module.ModVar):
            return "variable", value.span
        if isinstance(value, typs.StructTyp):
            return "type", value.span
        if isinstance(value, ir_traits.Trait):
            return "trait", value.span
        return None

    @staticmethod
    def _resolve_path_segment(
        ns: Env.Namespace,
        scope: Env | ir_module.Mod | typs.StructTyp,
        ident: ast.Ident,
    ) -> Any:
        match scope:
            case Env():
                res = scope.get(ns, ident.name)
            case ir_module.Mod():
                item = scope.get_item(ns, ident.name)
                if item is None or item.access == ir_module.PUBLIC:
                    res = opt_map(item, lambda i: i.value)
                else:
                    diag_info = Env._private_item_diag_info(item.value)
                    if diag_info is None:
                        res = None
                    else:
                        item_kind, defn_span = diag_info
                        raise PrivateItemAccessError(item_kind, ident.name, ident.span, defn_span)
            case typs.StructTyp():
                # A struct's members share one namespace and are all values,
                # never types, so a type lookup can't name one. Of those
                # members only associated functions are reachable by path:
                # `SomeStruct::x` is not a way to name a field, so a field
                # falls through to "not found" below.
                res = scope.get_assoc_fn(ident.name) if ns == Env.Namespace.VARS else None
                # Private associated functions are invisible outside the
                # struct's own module, same as private Mod items above.
                if res is not None and not res.is_accessible_from(ident.span.file):
                    raise PrivateItemAccessError("function", ident.name, ident.span, res.span)

        if res is None:
            raise ItemNotFoundError(ns.item_kind(), ident.name, ident.span)

        return res

    def resolve_typ(self, path: ast.Path) -> typs.Typ:
        """Resolve a (possibly qualified) path to a type.

        Only the path's *final* segment has to name a type: earlier
        segments are resolved by :meth:`resolve_path`, which is what lets
        a module qualify a path (``some_mod::SomeStruct``) even though a
        module isn't a type.

        :param path: The path to resolve, e.g. ``some_mod::SomeStruct``.
        :return: The type the path refers to.
        :raises ItemNotFoundError: If any segment of the path cannot be
            resolved.
        :raises PrivateItemAccessError: If the path resolves to a private
            type, accessed from outside the module it's defined in.
        :raises ModUsedAsTypError: If the path resolves to a module, which
            shares a namespace with types but isn't one.
        :raises TraitUsedAsTypError: If the path resolves to a trait,
            which shares a namespace with types but isn't one - only a
            bound on one, or an ``impl ... for ...`` target.
        """
        item = self.resolve_path(Env.Namespace.CONTAINERS, path)
        if isinstance(item, ir_module.Mod):
            raise ModUsedAsTypError(item.name, path.idents[-1].span)
        if isinstance(item, ir_traits.Trait):
            raise TraitUsedAsTypError(item.name, path.idents[-1].span)
        return checked_cast(item, typs.Typ)

    def resolve_path(self, ns: Env.Namespace, path: ast.Path) -> Any:
        """Resolve a (possibly qualified) path to an item in ``ns``.

        All but the last segment of ``path`` are resolved in the container
        namespace (module or struct names), giving the scope the final
        segment is looked up in.

        :param ns: The namespace the final path segment is looked up in.
        :param path: The path to resolve.
        :return: The item the path refers to.
        :raises ItemNotFoundError: If any segment of the path cannot be
            resolved.
        :raises PrivateItemAccessError: If any segment of the path resolves
            to a private function, variable, or type, accessed from outside
            the module it (or, for an associated function, its struct) is
            defined in.
        """
        assert_ge(len(path.idents), 1)

        scope = self
        for ident in path.idents[:-1]:
            scope = Env._resolve_path_segment(Env.Namespace.CONTAINERS, scope, ident)

        return Env._resolve_path_segment(ns, scope, path.idents[-1])
