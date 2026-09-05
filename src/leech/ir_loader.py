# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Loading the set of modules that make up one compilation."""

import functools
import pathlib
from collections.abc import Collection, Sequence
from typing import Final, Optional

from leech import asserts, ast, compilation, errors, ir_env, ir_module, ir_traits, parse, src

#: Resolved package directory containing the bundled standard library.
_BUNDLED_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).parent.resolve()


@functools.cache
def _parse_bundled_mod_ast(path: pathlib.Path) -> ast.Mod:
    """Parse and process-cache a bundled source file's immutable AST."""
    return parse.parse_mod_ast(src.SrcFile(path))


class ModLoader:
    """Load and path-deduplicate the modules in one compilation.

    The loader owns program-wide builtins and trait implementations. Imports search the
    importing file's directory, the bundled library, then ``extra_search_roots``.
    """

    _mods: Final[dict[pathlib.Path, ir_module.Mod]]
    ctx: Final[compilation.Ctx]
    impl_registry: Final[ir_traits.ImplRegistry]
    _extra_search_roots: Final[Sequence[pathlib.Path]]
    _prelude: Optional[ir_module.Mod]
    size_of_intrinsic: Final[ir_module.IntrinsicFnSymbol]
    ptr_cast_mut_intrinsic: Final[ir_module.IntrinsicFnSymbol]
    is_null_intrinsic: Final[ir_module.IntrinsicFnSymbol]
    enum_to_int_intrinsic: Final[ir_module.IntrinsicFnSymbol]

    def __init__(self, extra_search_roots: Sequence[pathlib.Path] = ()) -> None:
        # Deferred because intrinsic classes subclass ir_module.IntrinsicFnSymbol.
        from leech import ir_builtins  # noqa: PLC0415

        self._mods = {}
        self.ctx = compilation.Ctx()
        self.impl_registry = ir_traits.ImplRegistry(self.ctx)
        self._extra_search_roots = extra_search_roots

        # Built before the prelude (below): Mod.__init__ binds these into
        # every module's builtin_env, the prelude module's own included,
        # so they must already exist by the time it's loaded.
        builtin_env = ir_env.Env(self.ctx, self.impl_registry, None)
        self.size_of_intrinsic = ir_builtins.SizeOfIntrinsicFn(builtin_env)
        self.ptr_cast_mut_intrinsic = ir_builtins.PtrCastMutIntrinsicFn(builtin_env)
        self.is_null_intrinsic = ir_builtins.IsNullIntrinsicFn(builtin_env)
        self.enum_to_int_intrinsic = ir_builtins.EnumToIntIntrinsicFn(builtin_env)

        # Set to None first, so building the prelude module itself (below)
        # sees `self.prelude is None` and skips injecting the prelude into
        # its own builtin_env - otherwise loading the prelude would need
        # to load the prelude, forever. Nothing else is special-cased by
        # name or path; this is the only thing that breaks the cycle.
        self._prelude = None
        self._prelude = self.load(_BUNDLED_ROOT / "std" / "prelude.leech", "prelude")

    @property
    def prelude(self) -> Optional[ir_module.Mod]:
        """The bundled prelude, or ``None`` while it is being built."""
        return self._prelude

    @property
    def prelude_panic_ref(self) -> Optional[ir_module.FnRef]:
        """Return the prelude's unshadowable ``panic`` function when available.

        This property must not cache the temporary ``None`` observed during prelude loading.
        """
        if self._prelude is None:
            return None
        item = self._prelude.get_item(ir_env.Env.Namespace.VARS, "panic")
        if item is None:
            return None
        panic_symbol = asserts.checked_cast(item.value, ir_module.SrcFnSymbol)
        return panic_symbol.instantiate(()).ref

    def resolve_import(
        self, importing_file: src.SrcFile, path: ast.Path
    ) -> tuple[pathlib.Path, str]:
        """Resolve an import to its first matching file and qualified name."""
        seg_with_args = next((seg for seg in path.segs if seg.comptime_args), None)
        if seg_with_args is not None:
            raise errors.ComptimeArgsOnNonGenericItemError(
                seg_with_args.ident.name, seg_with_args.span
            )

        idents = [seg.ident.name for seg in path.segs]
        roots = (importing_file.path.parent, _BUNDLED_ROOT, *self._extra_search_roots)
        for root in roots:
            candidate = root.joinpath(*idents[:-1], f"{idents[-1]}.leech")
            if candidate.exists():
                return candidate, "::".join(idents)
        raise errors.ModDoesNotExistError(path.str(), path.span)

    def load(self, path: pathlib.Path, qualified_name: str) -> ir_module.Mod:
        """Load a path-deduplicated module, using the first qualified name seen."""
        key = path.resolve()
        cached = self._mods.get(key)
        if cached is not None:
            return cached

        if key.is_relative_to(_BUNDLED_ROOT):
            mod_ast = _parse_bundled_mod_ast(key)
        else:
            mod_ast = parse.parse_mod_ast(src.SrcFile(path))
        mod = ir_module.Mod(qualified_name, mod_ast, self)
        # Registered *before* building, so a module reached again while
        # it's still being built - i.e. an import cycle - gets this same
        # object back instead of recursing forever. Its `items` are
        # still filling up at that point, but nothing consults an
        # imported module's items during building: signatures, struct
        # fields and initializers all resolve lazily, long after every
        # module in the cycle is complete.
        self._mods[key] = mod
        mod.build()
        return mod

    def check_declarations(self) -> None:
        """Type-check every declaration after the complete module graph is loaded.

        Comptime parameter declarations are validated first: a bound or a
        declared value type may name any item in the graph, including one
        declared after the parameter using it.
        """
        for comptime_param in self.ctx.declared_comptime_params():
            comptime_param.check_declaration()
        for mod in self._mods.values():
            mod.check_declarations()

    @property
    def mods(self) -> Collection[ir_module.Mod]:
        """Every module loaded so far, in the order they were first reached."""
        return self._mods.values()
