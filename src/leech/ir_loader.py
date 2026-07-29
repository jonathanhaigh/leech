"""Loading the set of modules that make up one compilation."""

from collections.abc import Collection
from pathlib import Path
from typing import Final

from leech import ir_module, parse
from leech.src import SrcFile


class ModLoader:
    """Loads and caches the modules making up a single compilation.

    A module is identified by its resolved path, so a file reached by
    several import paths (e.g. a "diamond" import graph) is parsed and
    lowered exactly once, and every reference to it shares one
    :class:`~leech.ir_module.Mod` - and therefore one set of
    :class:`~leech.typs.StructTyp` instances, which are compared by identity.

    A loader is single-use in practice: one per invocation of
    :func:`leech.main.compile_to_llvm_ir`.
    """

    _mods: Final[dict[Path, ir_module.Mod]]

    def __init__(self) -> None:
        self._mods = {}

    def load(self, path: Path) -> ir_module.Mod:
        """Load the module at ``path``, or return the already-loaded one.

        :param path: The source file to load. Resolved before use, so
            paths that differ only in form (``./a.leech`` vs ``a.leech``) name
            the same module.
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

        mod_ast = parse.parse_mod_ast(SrcFile(path))
        mod = ir_module.Mod(path.stem, mod_ast, self)
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
