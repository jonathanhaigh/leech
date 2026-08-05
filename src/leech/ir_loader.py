# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Loading the set of modules that make up one compilation."""

import functools
import pathlib
from collections.abc import Collection, Sequence
from typing import Final, Optional

from leech import asserts, ast, errors, ir_env, ir_module, ir_traits, parse, src

#: The directory holding the bundled standard library (``std::...``
#: imports), shipped alongside the installed ``leech`` package - the same
#: ``pathlib.Path(__file__).parent``-relative pattern :mod:`leech.parse`
#: already uses to find ``leech.lark``. Resolved once here so it compares
#: reliably against the already-resolved paths :meth:`ModLoader.load`
#: checks it against.
_BUNDLED_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).parent.resolve()


@functools.cache
def _parse_bundled_mod_ast(path: pathlib.Path) -> ast.Mod:
    """Parse a bundled standard-library file's AST once per process.

    Every :class:`ModLoader` instance re-loads the same handful of bundled
    ``std/`` files (the prelude always, others on import) - safe to share
    process-wide since parsing is pure and an installed package's own
    files don't change during a process's lifetime, unlike a project's own
    source (reached through the importing file's directory or an
    ``--extra-search-root``, never cached here). Sharing the parsed AST
    only - not the built :class:`~leech.ir_module.Mod` - keeps every
    :class:`ModLoader` fully independent downstream: type-check results
    live in an out-of-band side table rather than on the AST (see
    :mod:`leech.typcheck`), so two builds from the same AST never see each
    other's state.

    :param path: An already-resolved path known to be under
        :data:`_BUNDLED_ROOT`.
    :return: The parsed AST.
    """
    return parse.parse_mod_ast(src.SrcFile(path))


class ModLoader:
    """Loads and caches the modules making up a single compilation.

    A module is identified by its resolved path, so a file reached by
    several import paths (e.g. a "diamond" import graph) is parsed and
    lowered exactly once, and every reference to it shares one
    :class:`~leech.ir_module.Mod` - and therefore one set of
    :class:`~leech.typs.StructTyp` instances, which are compared by identity.

    Also owns :attr:`impl_registry`, the program-wide index of every trait
    impl across every module - it lives here, rather than on
    :class:`~leech.ir_module.Mod`, because it's the one object that sees
    every module regardless of which one a lookup starts from.

    A loader is single-use in practice: one per invocation of
    :func:`leech.driver.compile_to_llvm_ir`.

    :param extra_search_roots: Additional directories to search for an
        import that isn't found next to the importing file - tried in
        order, after the importing file's own directory. Empty by
        default; not yet wired to any CLI flag or environment variable
        (a future ``--search-path``/env-var option would populate this).
    """

    _mods: Final[dict[pathlib.Path, ir_module.Mod]]
    impl_registry: Final[ir_traits.ImplRegistry]
    _extra_search_roots: Final[Sequence[pathlib.Path]]
    _prelude: Optional[ir_module.Mod]
    size_of_builtin: Final[ir_module.GenericBuiltinFn]
    ptr_cast_mut_builtin: Final[ir_module.GenericBuiltinFn]
    is_null_builtin: Final[ir_module.GenericBuiltinFn]

    def __init__(self, extra_search_roots: Sequence[pathlib.Path] = ()) -> None:
        # Deferred to break an import cycle - see ir_builtins.py's own
        # module docstring.
        from leech import ir_builtins  # noqa: PLC0415

        self._mods = {}
        self.impl_registry = ir_traits.ImplRegistry()
        self._extra_search_roots = extra_search_roots

        # Built before the prelude (below): Mod.__init__ binds these three
        # into every module's builtin_env, the prelude module's own
        # included, so they must already exist by the time it's loaded.
        builtin_env = ir_env.Env(impl_registry=self.impl_registry)
        self.size_of_builtin = ir_builtins.SizeOfBuiltinFn(builtin_env)
        self.ptr_cast_mut_builtin = ir_builtins.PtrCastMutBuiltinFn(builtin_env)
        self.is_null_builtin = ir_builtins.IsNullBuiltinFn(builtin_env)

        # Set to None first, so building the prelude module itself (below)
        # sees `self.prelude is None` and skips injecting the prelude into
        # its own builtin_env - otherwise loading the prelude would need
        # to load the prelude, forever. Nothing else is special-cased by
        # name or path; this is the only thing that breaks the cycle.
        self._prelude = None
        self._prelude = self.load(_BUNDLED_ROOT / "std" / "prelude.leech", "prelude")

    @property
    def prelude(self) -> Optional[ir_module.Mod]:
        """The bundled prelude module, whose public items are auto-bound
        into every other module's scope (see
        :meth:`~leech.ir_module.Mod.__init__`).

        ``None`` only while the prelude module itself is being built -
        see :meth:`__init__`.
        """
        return self._prelude

    @property
    def prelude_panic_fn(self) -> Optional[ir_module.FnSpec]:
        """The bundled prelude's own ``panic`` function - see
        :attr:`~leech.ir_env.Env.panic_fn`, which this feeds every
        module's top-level scope with.

        Not cached (unlike :attr:`prelude`, whose identity is stable once
        set): the prelude module's own ``Mod.__init__`` reads this - via
        :attr:`~leech.ir_env.Env.panic_fn` on the very ``builtin_env`` it's
        constructing - while :attr:`prelude` is still ``None``, so a
        cached ``None`` from that first read would stick forever and break
        every other module's.

        ``None`` only while the prelude module itself is being built -
        see :meth:`__init__` - since nothing needs it before then either.
        """
        if self._prelude is None:
            return None
        item = self._prelude.get_item(ir_env.Env.Namespace.VARS, "panic")
        if item is None:
            return None
        return asserts.checked_cast(item.value, ir_module.FnSpec)

    @property
    def builtins(self) -> tuple[ir_module.GenericBuiltinFn, ...]:
        """Every compiler-intrinsic builtin this loader owns, for codegen's
        instance discovery (see
        :meth:`~leech.codegen.Compiler._discover_fn_instances`) - a
        builtin has no :class:`~leech.ir_module.ModItem` of its own to be
        found through, unlike a real generic function.
        """
        return (self.size_of_builtin, self.ptr_cast_mut_builtin, self.is_null_builtin)

    def resolve_import(
        self, importing_file: src.SrcFile, path: ast.Path
    ) -> tuple[pathlib.Path, str]:
        """Resolve an ``import`` path to the file it names and its qualified name.

        Tries, in order, the importing file's own directory (so a plain
        sibling-file import like ``import a;`` resolves exactly as it
        always has, including for an import written inside a file reached
        via :data:`_BUNDLED_ROOT` or :attr:`_extra_search_roots` - it's
        resolved relative to *that* file's own directory, not the original
        root's), then :data:`_BUNDLED_ROOT` (the installed standard
        library), then each of :attr:`_extra_search_roots`. The first
        candidate that exists wins.

        :param importing_file: The source file the ``import`` statement
            appears in.
        :param path: The parsed ``::``-separated import path.
        :return: The resolved file path, and the ``::``-separated
            qualified name (``"std::mem"`` for ``std::mem``) to give the
            resulting :class:`~leech.ir_module.Mod` - used to qualify its
            items' mangled symbol names (see
            :attr:`~leech.ir_module.ModItem.qualified_name`).
        :raises ModDoesNotExistError: If no search root has a matching file.
        """
        idents = [ident.name for ident in path.idents]
        roots = (importing_file.path.parent, _BUNDLED_ROOT, *self._extra_search_roots)
        for root in roots:
            candidate = root.joinpath(*idents[:-1], f"{idents[-1]}.leech")
            if candidate.exists():
                return candidate, "::".join(idents)
        raise errors.ModDoesNotExistError(path.str(), path.span)

    def load(self, path: pathlib.Path, qualified_name: str) -> ir_module.Mod:
        """Load the module at ``path``, or return the already-loaded one.

        :param path: The source file to load. Resolved before use, so
            paths that differ only in form (``./a.leech`` vs ``a.leech``) name
            the same module.
        :param qualified_name: The name to give the module if this is the
            first time it's loaded - used to qualify its items' mangled
            symbol names (see
            :attr:`~leech.ir_module.ModItem.qualified_name`). Ignored on a
            cache hit, the same "first path reached wins" precedent as an
            ordinary diamond import.
        :return: The module, fully built unless it is currently being
            built further up the call stack (see below).
        :raises UnexpectedCharacterError: If a character in the file
            can't start any valid token.
        :raises UnexpectedTokenError: If a token in the file doesn't fit
            the grammar at that point.

        :post: load(a) is load(b) for a.resolve() == b.resolve() [cache]
        """
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

    @property
    def mods(self) -> Collection[ir_module.Mod]:
        """Every module loaded so far, in the order they were first reached."""
        return self._mods.values()
