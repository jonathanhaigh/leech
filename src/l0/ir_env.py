import enum
import re
from typing import Any, ChainMap, Optional, cast

from l0 import ir_module, ir_values
from l0 import l0ast as ast
from l0 import typs
from l0.asserts import assert_ge
from l0.l0errors import DuplicateItemDefnError, ItemNotFoundError


class Env:
    class Namespace(enum.Enum):
        VARS = 0
        TYPS = 1

        def item_kind(self) -> str:
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
        return Env(self)

    def get(self, ns: Env.Namespace, name: str) -> Any:
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
        key = (ns, name)
        if key in self.items.maps[0]:
            existing = self.items[key]
            raise DuplicateItemDefnError(
                ns.item_kind(), name, item.span, ast.opt_span(existing)
            )
        self.items[key] = item

    def get_var(self, name: str) -> Optional[ir_values.Value[typs.PtrTyp]]:
        return self.get(Env.Namespace.VARS, name)

    def add_var(self, name: str, var: ir_values.Value[typs.PtrTyp]) -> None:
        return self.add(Env.Namespace.VARS, name, var)

    def get_typ(self, name: str) -> Optional[typs.Typ]:
        return self.get(Env.Namespace.TYPS, name)

    def add_typ(self, name: str, typ: typs.Typ) -> None:
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

        if res is None:
            raise ItemNotFoundError(ns.item_kind(), ident.name, ident.span)

        return res

    def resolve_var(self, path: ast.Path) -> ir_values.Value[typs.PtrTyp]:
        return cast(
            ir_values.Value[typs.PtrTyp], self.resolve_path(Env.Namespace.VARS, path)
        )

    def resolve_typ(self, path: ast.Path) -> typs.Typ:
        return cast(typs.Typ, self.resolve_path(Env.Namespace.TYPS, path))

    def resolve_path(self, ns: Env.Namespace, path: ast.Path) -> Any:
        assert_ge(len(path.idents), 1)

        container = self
        for ident in path.idents[:-1]:
            container = Env._resolve_path_segment(Env.Namespace.TYPS, container, ident)

        return Env._resolve_path_segment(ns, container, path.idents[-1])
