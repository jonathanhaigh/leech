# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Loading the set of modules that make up one compilation."""

import pathlib
from collections.abc import Collection, Sequence
from typing import Final, Optional

from leech import ast, errors, ir_module, ir_traits, parse, src

#: The directory holding the bundled standard library (``std::...``
#: imports), shipped alongside the installed ``leech`` package - the same
#: ``pathlib.Path(__file__).parent``-relative pattern :mod:`leech.parse`
#: already uses to find ``leech.lark``.
_BUNDLED_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).parent


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

    def __init__(self, extra_search_roots: Sequence[pathlib.Path] = ()) -> None:
        self._mods = {}
        self.impl_registry = ir_traits.ImplRegistry()
        self._extra_search_roots = extra_search_roots
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
        :return: The resolved file path, and the dotted qualified name
            (``"std.mem"`` for ``std::mem``) to give the resulting
            :class:`~leech.ir_module.Mod` - used to qualify its items'
            mangled symbol names (see
            :attr:`~leech.ir_module.ModItem.qualified_name`).
        :raises ModDoesNotExistError: If no search root has a matching file.
        """
        idents = [ident.name for ident in path.idents]
        roots = (importing_file.path.parent, _BUNDLED_ROOT, *self._extra_search_roots)
        for root in roots:
            candidate = root.joinpath(*idents[:-1], f"{idents[-1]}.leech")
            if candidate.exists():
                return candidate, ".".join(idents)
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
        """
        key = path.resolve()
        cached = self._mods.get(key)
        if cached is not None:
            return cached

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
