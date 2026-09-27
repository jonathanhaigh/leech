# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import dataclasses
import functools
import pathlib
import signal
import subprocess
import types
from collections.abc import Mapping
from typing import ClassVar, Final, Optional

from leech import ast, driver, ir_module, parse, reserved
from leech import src as leech_src

_TOOL_TIMEOUT_SECONDS = 30


@functools.cache
def _bundled_mod_llvm_ir() -> Mapping[str, str]:
    std_root = pathlib.Path(driver.__file__).parent / "std"
    compiled = dict[str, str]()
    for path in sorted(std_root.glob("*.leech")):
        name = "prelude" if path.stem == "prelude" else f"std::{path.stem}"
        compiled[name] = driver.compile_to_llvm_ir(leech_src.SrcFile(path), name)
    return types.MappingProxyType(compiled)


def src_position(src: str, substring: str) -> tuple[int, int]:
    """Return the one-based location of ``substring``'s first occurrence."""
    index = src.find(substring)
    assert index >= 0, f"substring {substring!r} not present in the source"
    preceding = src[:index]
    line = preceding.count("\n") + 1
    column = index - preceding.rfind("\n")
    return line, column


def assert_span_at(
    span: Optional[leech_src.SrcSpan], src: str, substring: str
) -> leech_src.SrcSpan:
    """Assert that ``span`` starts at ``substring``'s first occurrence and return it."""
    expected = src_position(src, substring)
    assert span is not None, f"expected span at {expected} for {substring!r}, got no span"
    assert span.file.src == src, (
        f"span file {span.file.path} does not match the supplied source containing {substring!r}"
    )
    actual = (span.start_line, span.start_col)
    assert actual == expected, (
        f"expected span in {span.file.path} at {expected} for {substring!r}, got {actual}"
    )
    return span


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
        if path.is_relative_to(self.workspace / ".bundled"):
            raise ValueError(f"module path uses reserved harness directory: {str(mod.path)!r}")
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
        """Materialize and parse one module through the production parser boundary."""
        mod = self._coerce_mod(src_or_mod)
        path = self.write_mod(mod)
        return parse.parse_mod_ast(leech_src.SrcFile(path))

    def build(self, program: str | TestProgram) -> ir_module.Mod:
        """Build semantic IR for the root after materializing every supplied module."""
        program = self._coerce_program(program)
        paths = self._materialize(program)
        return driver.compile_to_ir(leech_src.SrcFile(paths[program.root.name]), program.root.name)

    def compile(self, program: str | TestProgram) -> CompiledProgram:
        """Compile every supplied module independently under its declared name."""
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

    def _tool_failure(
        self,
        summary: str,
        failure: subprocess.CompletedProcess[str] | subprocess.TimeoutExpired,
    ) -> AssertionError:
        def format_stream(stream: Optional[str | bytes]) -> str:
            if stream is None:
                return ""
            if isinstance(stream, bytes):
                return stream.decode(errors="replace")
            return stream

        if isinstance(failure, subprocess.TimeoutExpired):
            command = failure.cmd
            returncode = None
            stdout = failure.stdout
            stderr = failure.stderr
        else:
            command = failure.args
            returncode = failure.returncode
            stdout = failure.stdout
            stderr = failure.stderr

        return AssertionError(
            f"{summary}\n"
            f"command: {command!r}\n"
            f"return code: {returncode!r}\n"
            f"stdout:\n{format_stream(stdout)}\n"
            f"stderr:\n{format_stream(stderr)}\n"
            f"workspace: {self.workspace}"
        )

    def _invoke_tool(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=_TOOL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise self._tool_failure(
                f"command timed out after {_TOOL_TIMEOUT_SECONDS} seconds",
                exc,
            ) from exc

    def _link(self, compiled: CompiledProgram) -> pathlib.Path:
        llvm_paths = [mod.llvm_path for mod in compiled.mods.values()]
        bundled_root = self.workspace / ".bundled"
        for name, llvm_ir in _bundled_mod_llvm_ir().items():
            if name != "prelude" and name in compiled.mods:
                continue
            llvm_path = bundled_root.joinpath(*name.split("::")).with_suffix(".ll")
            self._write_src(llvm_path, llvm_ir)
            llvm_paths.append(llvm_path)

        bitcode_path = self.workspace / "program.bc"
        command = ["llvm-link", "-o", str(bitcode_path), *(str(path) for path in llvm_paths)]
        result = self._invoke_tool(command)
        if result.returncode != 0:
            raise self._tool_failure(
                "llvm-link failed",
                result,
            )
        return bitcode_path

    def run(self, program: str | TestProgram) -> subprocess.CompletedProcess[str]:
        """Run a ``main``-rooted program with unshadowed bundled modules linked."""
        program = self._coerce_program(program)
        if program.root.name != "main":
            raise ValueError("runnable program root module must be named 'main'")
        compiled = self.compile(program)
        bitcode_path = self._link(compiled)
        return self._invoke_tool(["lli", "--disable-symbolication", str(bitcode_path)])

    def check(
        self,
        program: str | TestProgram,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_status: int = 0,
    ) -> None:
        """Assert exact streams and status, defaulting to empty streams and success."""
        result = self.run(program)
        assert result.stdout == stdout, (
            f"unexpected stdout: expected {stdout!r}, got {result.stdout!r}; "
            f"result: {result!r}; workspace: {self.workspace}"
        )
        assert result.stderr == stderr, (
            f"unexpected stderr: expected {stderr!r}, got {result.stderr!r}; "
            f"result: {result!r}; workspace: {self.workspace}"
        )
        assert result.returncode == exit_status, (
            f"unexpected exit status: expected {exit_status!r}, got {result.returncode!r}; "
            f"result: {result!r}; workspace: {self.workspace}"
        )

    def check_signal(
        self,
        program: str | TestProgram,
        *,
        expected_signal: signal.Signals,
        stderr_prefix: str,
        stdout: str = "",
    ) -> None:
        """Assert a signal exit, exact stdout, and the stable prefix of LLVM's stderr."""
        result = self.run(program)
        assert result.stdout == stdout, (
            f"unexpected stdout: expected {stdout!r}, got {result.stdout!r}; "
            f"result: {result!r}; workspace: {self.workspace}"
        )
        assert result.stderr.startswith(stderr_prefix), (
            f"unexpected stderr prefix: expected {stderr_prefix!r}, got {result.stderr!r}; "
            f"result: {result!r}; workspace: {self.workspace}"
        )
        expected_returncode = -expected_signal.value
        assert result.returncode == expected_returncode, (
            f"unexpected signal status: expected {expected_returncode!r}, "
            f"got {result.returncode!r}; result: {result!r}; workspace: {self.workspace}"
        )

    def write_mod(self, mod: ModSrc) -> pathlib.Path:
        """Materialize one module without invoking a compiler phase."""
        path = self._mod_path(mod)
        self._write_src(path, mod.src)
        return path
