"""Scope and name resolution for variables and types."""

import enum
import re
from typing import Any, ChainMap, Optional, cast

from l0 import ir_module, ir_values
from l0 import l0ast as ast
from l0 import typs
from l0.asserts import assert_ge
from l0.l0errors import DuplicateItemDefnError, ItemNotFoundError


class Env:
    """A lexical scope mapping names to variables and types.

    Scopes are chained: a child scope (see :meth:`new_child`) sees its own
    bindings plus everything visible in its ancestors, and shadows
    ancestor bindings of the same name. Variables and types are looked up
    in separate namespaces (see :class:`Namespace`), so a variable and a
    type may share a name without conflict.

    :param parent: The enclosing scope, or ``None`` to create a fresh,
        top-level scope.
    """

    class Namespace(enum.Enum):
        """Which kind of item a name is being looked up or bound in."""

        VARS = 0
        TYPS = 1

        def item_kind(self) -> str:
            """A human-readable label for this namespace, for diagnostics."""
            match self:
                case Env.Namespace.VARS:
                    return "variable"
                case Env.Namespace.TYPS:
                    return "type"

    items: ChainMap[tuple[Env.Namespace, str], typs.Typ | ir_values.Value[typs.PtrTyp]]

    def __init__(self, parent: Optional[Env] = None) -> None:
        if parent is None:
            self.items = ChainMap()
        else:
            self.items = parent.items.new_child()

    def new_child(self) -> Env:
        """Create a new scope nested inside this one.

        :return: A fresh scope that inherits this scope's bindings.
        """
        return Env(self)

    def get(self, ns: Env.Namespace, name: str) -> Any:
        """Look up ``name`` in ``ns``, searching outward through parent scopes.

        Integer type names (e.g. ``i32``, ``u8``) are recognized and
        created on demand when looked up in the type namespace.

        :param ns: The namespace to search in.
        :param name: The name to look up.
        :return: The bound item, or ``None`` if ``name`` is not bound.
        """
        key = (ns, name)
        if key in self.items:
            return self.items[key]

        if ns == Env.Namespace.TYPS:
            m = re.fullmatch("([iu])([1-9][0-9]*)", name)
            if m:
                signage = typs.SIGNED if m[1] == "i" else typs.UNSIGNED
                width = int(m[2])
                typ = typs.IntTyp.get_or_create(width, signage)
                self.items.maps[-1][key] = typ
                return typ

        return None

    def add(self, ns: Env.Namespace, name: str, item: Any) -> None:
        """Bind ``name`` to ``item`` in ``ns``, in this scope only.

        :param ns: The namespace to bind in.
        :param name: The name to bind.
        :param item: The item to bind ``name`` to.
        :raises DuplicateItemDefnError: If ``name`` is already bound in
            ``ns`` in this scope.
        """
        key = (ns, name)
        if key in self.items.maps[0]:
            existing = self.items[key]
            raise DuplicateItemDefnError(
                ns.item_kind(), name, item.span, ast.opt_span(existing)
            )
        self.items[key] = item

    def get_var(self, name: str) -> Optional[ir_values.Value[typs.PtrTyp]]:
        """Look up a variable by name.

        :param name: The variable name to look up.
        :return: The bound variable, or ``None`` if not bound.
        """
        return self.get(Env.Namespace.VARS, name)

    def add_var(self, name: str, var: ir_values.Value[typs.PtrTyp]) -> None:
        """Bind a variable name in this scope.

        :param name: The name to bind.
        :param var: The variable to bind ``name`` to.
        :raises DuplicateItemDefnError: If ``name`` is already bound to a
            variable in this scope.
        """
        return self.add(Env.Namespace.VARS, name, var)

    def get_typ(self, name: str) -> Optional[typs.Typ]:
        """Look up a type by name.

        :param name: The type name to look up.
        :return: The bound type, or ``None`` if not bound.
        """
        return self.get(Env.Namespace.TYPS, name)

    def add_typ(self, name: str, typ: typs.Typ) -> None:
        """Bind a type name in this scope.

        :param name: The name to bind.
        :param typ: The type to bind ``name`` to.
        :raises DuplicateItemDefnError: If ``name`` is already bound to a
            type in this scope.
        """
        return self.add(Env.Namespace.TYPS, name, typ)

    @staticmethod
    def _resolve_path_segment(
        ns: Env.Namespace,
        container: Env | ir_module.Mod | typs.StructTyp,
        ident: ast.Ident,
    ) -> Any:
        match container:
            case Env():
                res = container.get(ns, ident.name)
            case ir_module.Mod():
                res = next(
                    (
                        item.value
                        for item in container.items
                        if item.access == ir_module.PUBLIC and item.name == ident.name
                    ),
                    None,
                )
            case typs.StructTyp():
                # Only the struct's own scope - not its parents' scopes, which
                # the struct's Env inherits from for resolving field types.
                res = container.env.items.maps[0].get((ns, ident.name))
                # Everything reachable here is an associated function (see
                # Mod._build_impl_defn); private ones are invisible outside
                # the struct's own module, same as private Mod items above.
                if isinstance(res, ir_module.Fn) and not res.is_accessible_from(
                    ident.span.file
                ):
                    res = None

        if res is None:
            raise ItemNotFoundError(ns.item_kind(), ident.name, ident.span)

        return res

    def resolve_var(self, path: ast.Path) -> ir_values.Value[typs.PtrTyp]:
        """Resolve a (possibly qualified) path to a variable.

        :param path: The path to resolve, e.g. ``some_mod::some_var``.
        :return: The variable the path refers to.
        :raises ItemNotFoundError: If any segment of the path cannot be
            resolved.
        """
        return cast(
            ir_values.Value[typs.PtrTyp], self.resolve_path(Env.Namespace.VARS, path)
        )

    def resolve_typ(self, path: ast.Path) -> typs.Typ:
        """Resolve a (possibly qualified) path to a type.

        :param path: The path to resolve, e.g. ``some_mod::SomeStruct``.
        :return: The type the path refers to.
        :raises ItemNotFoundError: If any segment of the path cannot be
            resolved.
        """
        return cast(typs.Typ, self.resolve_path(Env.Namespace.TYPS, path))

    def resolve_path(self, ns: Env.Namespace, path: ast.Path) -> Any:
        """Resolve a (possibly qualified) path to an item in ``ns``.

        All but the last segment of ``path`` are resolved as types (module
        or struct names), used to find the container the final segment is
        looked up in.

        :param ns: The namespace the final path segment is looked up in.
        :param path: The path to resolve.
        :return: The item the path refers to.
        :raises ItemNotFoundError: If any segment of the path cannot be
            resolved.
        """
        assert_ge(len(path.idents), 1)

        container = self
        for ident in path.idents[:-1]:
            container = Env._resolve_path_segment(Env.Namespace.TYPS, container, ident)

        return Env._resolve_path_segment(ns, container, path.idents[-1])
