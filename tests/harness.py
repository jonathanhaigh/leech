# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import dataclasses
import pathlib
from typing import ClassVar, Final, Optional

from leech import parse, reserved


class ModSrc:
    """A Leech module description with statically final fields.

    With no explicit path, a name such as ``pkg::helper`` becomes
    ``pkg/helper.leech``. Only the final name segment must be non-reserved because
    intermediate segments represent package directories. Tests cannot replace the
    compiler-owned ambient prelude.
    """

    name: Final[str]
    src: Final[str]
    path: Final[pathlib.Path]

    def __init__(self, name: str, src: str, path: Optional[str] = None) -> None:
        segments = parse.parse_qualified_name(name)
        if segments is None:
            raise ValueError(f"invalid qualified module name: {name!r}")
        if name == "prelude":
            raise ValueError("tests cannot supply the ambient prelude module")
        if reserved.is_reserved(segments[-1]):
            raise ValueError(f"reserved final module name segment: {segments[-1]!r}")

        if path is None:
            mod_path = pathlib.Path(*segments).with_suffix(".leech")
        else:
            if not path:
                raise ValueError("module path must not be empty")
            mod_path = pathlib.Path(path)
        if mod_path.is_absolute():
            raise ValueError(f"module path must be relative: {mod_path.as_posix()!r}")
        if ".." in mod_path.parts:
            raise ValueError(f"module path must not contain '..': {mod_path.as_posix()!r}")
        if mod_path.suffix != ".leech":
            raise ValueError(f"module path must have a .leech suffix: {mod_path.as_posix()!r}")

        self.name = name
        self.src = src
        self.path = mod_path


@dataclasses.dataclass(frozen=True)
class TestProgram:
    """A root module and the supporting modules compiled with it."""

    # Prevent pytest from collecting this Test-prefixed model as a test class.
    __test__: ClassVar[bool] = False

    root: ModSrc
    mods: tuple[ModSrc, ...] = ()

    def __post_init__(self) -> None:
        mods = (self.root, *self.mods)
        names = [mod.name for mod in mods]
        if len(names) != len(set(names)):
            raise ValueError("test program contains duplicate module names")
        paths = [mod.path for mod in mods]
        if len(paths) != len(set(paths)):
            raise ValueError("test program contains duplicate module paths")

    @classmethod
    def from_main(cls, src: str, *mods: ModSrc) -> TestProgram:
        return cls(ModSrc("main", src), mods)


class CompilerHarness:
    """Compiler test operations scoped to one temporary workspace."""

    workspace: Final[pathlib.Path]

    def __init__(self, workspace: pathlib.Path) -> None:
        self.workspace = workspace

    @staticmethod
    def _coerce_program(program: str | TestProgram) -> TestProgram:
        if isinstance(program, str):
            return TestProgram.from_main(program)
        return program
