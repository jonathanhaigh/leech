# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Loading the set of modules that make up one compilation.

A module is identified by the package it belongs to and its path within that package: the
file ``<package root>/x/a.leech`` is the module ``x::a``. The bundled standard library is the
package ``std``, so its modules are named ``std::...``. The root package is anonymous: the
directory implied by the root module's qualified name.
"""

import dataclasses
import functools
import pathlib
from collections.abc import Collection
from typing import Final, Optional

from leech import asserts, ast, compilation, errors, ir_module, parse, src

#: Resolved package directory containing the bundled standard library.
_BUNDLED_ROOT: Final[pathlib.Path] = pathlib.Path(__file__).parent.resolve()

STD_PACKAGE_NAME: Final[str] = "std"
"""The bundled standard library's package name, the first segment of its module names."""


@dataclasses.dataclass(frozen=True)
class Package:
    """A directory whose ``.leech`` files are modules named by their paths within it."""

    #: The resolved package directory.
    root: pathlib.Path
    #: The prefix of its modules' qualified names, or ``None`` for an anonymous package.
    name: Optional[str]


_STD_PACKAGE: Final[Package] = Package(_BUNDLED_ROOT / STD_PACKAGE_NAME, STD_PACKAGE_NAME)


@dataclasses.dataclass(frozen=True)
class ModId:
    """A module's identity: its package and its path segments within that package."""

    package: Package
    path: tuple[str, ...]

    @property
    def qualified_name(self) -> str:
        """The name that qualifies the module's symbols, such as ``x::a`` or ``std::io``."""
        prefix = () if self.package.name is None else (self.package.name,)
        return "::".join((*prefix, *self.path))

    @property
    def file(self) -> pathlib.Path:
        """The module's source file."""
        return self.package.root.joinpath(*self.path[:-1], f"{self.path[-1]}.leech")


_PRELUDE_ID: Final[ModId] = ModId(_STD_PACKAGE, ("prelude",))


def root_package_dir(path: pathlib.Path, qualified_name: str) -> Optional[pathlib.Path]:
    """Return the package directory implied by naming the file ``path`` ``qualified_name``.

    The name's segments must match the file's last path components, as ``x::b`` matches
    ``<dir>/x/b.leech``. Returns ``None`` when they don't.
    """
    segments = qualified_name.split("::")
    path = path.resolve()
    if path.suffix != ".leech" or len(path.parts) <= len(segments):
        return None
    parts = (*path.parent.parts[len(path.parent.parts) - len(segments) + 1 :], path.stem)
    if parts != tuple(segments):
        return None
    return path.parents[len(segments) - 1]


@functools.cache
def _parse_bundled_mod_ast(path: pathlib.Path) -> ast.Mod:
    """Parse and process-cache a bundled source file's immutable AST."""
    return parse.parse_mod_ast(src.SrcFile(path))


class ModLoader:
    """Load and deduplicate the modules in one compilation.

    ``import std::...`` resolves in the bundled standard library, and any other import in the
    root package. The bundled prelude is loaded before any other module.
    """

    ctx: Final[compilation.Ctx]
    _mods: Final[dict[pathlib.Path, ir_module.Mod]]
    #: Each loaded module's file, by qualified name.
    _files: Final[dict[str, pathlib.Path]]
    #: The package that non-``std`` imports resolve in, once ``load_root`` sets it.
    _root_package: Optional[Package]
    _prelude: Optional[ir_module.Mod]
    _building_prelude: bool

    def __init__(self, ctx: compilation.Ctx) -> None:
        self.ctx = ctx
        self._mods = {}
        self._files = {}
        self._root_package = None
        self._prelude = None
        self._building_prelude = False

    @property
    def prelude(self) -> Optional[ir_module.Mod]:
        """The bundled prelude, loaded on first use, or ``None`` while it is being built.

        Building the prelude sees ``None``, so it doesn't inject the prelude into its own
        scope. Nothing else is special-cased by name or path; this is the only thing that
        breaks the cycle.
        """
        if self._prelude is None and not self._building_prelude:
            self._building_prelude = True
            try:
                self._prelude = self._load(_PRELUDE_ID)
            finally:
                self._building_prelude = False
        return self._prelude

    def load_root(self, path: pathlib.Path, qualified_name: str) -> ir_module.Mod:
        """Load the module being compiled, whose name must match its location.

        A file in the bundled library belongs to package ``std``. Any other file's package is
        the directory its name implies, and its name cannot start with ``std``.
        """
        _ = self.prelude
        assert len(self._mods) == 1, "the root must be loaded straight after the prelude"
        mod_id = self._mod_id_for_file(path)
        if mod_id is None:
            package_dir = root_package_dir(path, qualified_name)
            if package_dir is None:
                raise errors.ModNameLocationMismatchError(qualified_name, path)
            if qualified_name.split("::", maxsplit=1)[0] == STD_PACKAGE_NAME:
                raise errors.StdModNameReservedError(qualified_name, path)
            self._root_package = Package(package_dir, None)
            mod_id = asserts.checked_cast(self._mod_id_for_file(path), ModId)
        if mod_id.qualified_name != qualified_name:
            raise errors.ModNameLocationMismatchError(qualified_name, path)
        return self.load(mod_id)

    def resolve_import(self, path: ast.Path) -> ModId:
        """Resolve an import path to the module it names."""
        seg_with_args = next((seg for seg in path.segs if seg.comptime_args), None)
        if seg_with_args is not None:
            raise errors.ComptimeArgsOnNonGenericItemError(
                seg_with_args.ident.name, seg_with_args.span
            )

        idents = tuple(seg.ident.name for seg in path.segs)
        if idents[0] == STD_PACKAGE_NAME:
            candidates = [ModId(_STD_PACKAGE, idents[1:])] if len(idents) > 1 else []
        elif self._root_package is not None:
            candidates = [ModId(self._root_package, idents)]
        else:
            candidates = []
        for candidate in candidates:
            if candidate.file.is_file():
                # A link is named after the file it resolves to.
                mod_id = self._mod_id_for_file(candidate.file)
                if mod_id is None:
                    raise errors.ModOutsidePackagesError(path.str(), candidate.file, path.span)
                return mod_id
        raise errors.ModDoesNotExistError(path.str(), path.span)

    def load(self, mod_id: ModId) -> ir_module.Mod:
        """Load the module ``mod_id`` once, returning the same module on later requests."""
        _ = self.prelude
        return self._load(mod_id)

    def _load(self, mod_id: ModId) -> ir_module.Mod:
        key = mod_id.file.resolve()
        cached = self._mods.get(key)
        if cached is not None:
            return cached

        name = mod_id.qualified_name
        other_file = self._files.setdefault(name, key)
        assert other_file == key, f"modules {other_file} and {key} are both named {name}"
        if mod_id.package == _STD_PACKAGE:
            mod_ast = _parse_bundled_mod_ast(key)
        else:
            mod_ast = parse.parse_mod_ast(src.SrcFile(mod_id.file))
        mod = ir_module.Mod(name, mod_ast, self.ctx)
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

    def _mod_id_for_file(self, path: pathlib.Path) -> Optional[ModId]:
        """Name a file after the package containing it, preferring the bundled one."""
        path = path.resolve()
        packages = [_STD_PACKAGE]
        if self._root_package is not None:
            packages.append(self._root_package)
        for package in packages:
            if path.is_relative_to(package.root) and path.suffix == ".leech":
                relative = path.relative_to(package.root)
                return ModId(package, (*relative.parent.parts, relative.stem))
        return None

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
