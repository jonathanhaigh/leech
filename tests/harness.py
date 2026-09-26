# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import dataclasses
import pathlib
import types
from collections.abc import Mapping
from typing import ClassVar, Final, Optional

from leech import ast, driver, ir_module, parse, reserved
from leech import src as leech_src


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
            raise ValueError(f"module path must be relative: {str(mod_path)!r}")
        if ".." in mod_path.parts:
            raise ValueError(f"module path must not contain '..': {str(mod_path)!r}")
        if mod_path.suffix != ".leech":
            raise ValueError(f"module path must have a .leech suffix: {str(mod_path)!r}")

        self.name = name
        self.src = src
        self.path = mod_path

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, path={self.path!r})"


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


@dataclasses.dataclass(frozen=True)
class CompiledMod:
    """One compiled source module and its materialized LLVM artifact."""

    mod: ModSrc
    src_path: pathlib.Path
    llvm_path: pathlib.Path
    llvm_ir: str = dataclasses.field(repr=False)


class CompiledProgram:
    """Compiled modules keyed by qualified name in declaration order."""

    mods: Final[Mapping[str, CompiledMod]]

    def __init__(self, mods: Mapping[str, CompiledMod]) -> None:
        self.mods = types.MappingProxyType(dict(mods))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(mods={tuple(self.mods)!r})"


class CompilerHarness:
    """Compiler test operations scoped to one temporary workspace."""

    workspace: Final[pathlib.Path]

    def __init__(self, workspace: pathlib.Path) -> None:
        self.workspace = workspace.resolve()

    @staticmethod
    def _coerce_program(program: str | TestProgram) -> TestProgram:
        if isinstance(program, str):
            return TestProgram.from_main(program)
        return program

    @staticmethod
    def _coerce_mod(src_or_mod: str | ModSrc) -> ModSrc:
        if isinstance(src_or_mod, str):
            return ModSrc("main", src_or_mod)
        return src_or_mod

    def _mod_path(self, mod: ModSrc) -> pathlib.Path:
        path = (self.workspace / mod.path).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError(f"module path escapes the compiler workspace: {str(mod.path)!r}")
        return path

    @staticmethod
    def _write_src(path: pathlib.Path, src: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")

    def _materialize(self, program: TestProgram) -> dict[str, pathlib.Path]:
        mods = (program.root, *program.mods)
        paths = {mod.name: self._mod_path(mod) for mod in mods}
        for mod in mods:
            self._write_src(paths[mod.name], mod.src)
        return paths

    def parse(self, src_or_mod: str | ModSrc) -> ast.Mod:
        mod = self._coerce_mod(src_or_mod)
        path = self.write_mod(mod)
        return parse.parse_mod_ast(leech_src.SrcFile(path))

    def build(self, program: str | TestProgram) -> ir_module.Mod:
        program = self._coerce_program(program)
        paths = self._materialize(program)
        return driver.compile_to_ir(leech_src.SrcFile(paths[program.root.name]), program.root.name)

    def compile(self, program: str | TestProgram) -> CompiledProgram:
        program = self._coerce_program(program)
        src_paths = self._materialize(program)
        compiled = dict[str, CompiledMod]()
        for mod in (program.root, *program.mods):
            src_path = src_paths[mod.name]
            llvm_ir = driver.compile_to_llvm_ir(leech_src.SrcFile(src_path), mod.name)
            llvm_path = src_path.with_suffix(".ll")
            self._write_src(llvm_path, llvm_ir)
            compiled[mod.name] = CompiledMod(mod, src_path, llvm_path, llvm_ir)
        return CompiledProgram(compiled)

    def write_mod(self, mod: ModSrc) -> pathlib.Path:
        path = self._mod_path(mod)
        self._write_src(path, mod.src)
        return path
