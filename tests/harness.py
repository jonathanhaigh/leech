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

from leech import (
    ast,
    compilation,
    diag,
    errors,
    ir_module,
    ll_emit,
    mono,
    parse,
    reserved,
    toolchain,
)
from leech import program as leech_program
from leech import session as session_mod
from leech import src as leech_src
from leech.cli import common

_TOOL_TIMEOUT_SECONDS = 30
# Holds the linked program; no module path can reach it, since names are identifiers.
_LINK_DIR = ".link"


@functools.cache
def _bundled_mod_llvm_ir() -> Mapping[str, str]:
    std_root = pathlib.Path(leech_program.__file__).parent / "std"
    compiled = dict[str, str]()
    for path in sorted(std_root.glob("*.leech")):
        name = f"std::{path.stem}"
        checked = leech_program.check_module(path, session_mod.Session(), qualified_name=name)
        compiled[name] = checked.llvm_ir()
    return types.MappingProxyType(compiled)


def _session(diags: Optional[diag.Diags]) -> session_mod.Session:
    if diags is None:
        return session_mod.Session()
    return session_mod.Session(diags)


@dataclasses.dataclass(frozen=True)
class BuildResult:
    """The outcome of ``build_exe``, and every distinct diagnostic it produced in source order."""

    #: The absolute path of the built executable, or ``None`` if the build failed.
    exe: Optional[pathlib.Path]
    diags: tuple[errors.UserError, ...]


def build_exe(root: pathlib.Path) -> BuildResult:
    """Build an executable beside ``root``, named after its stem, as ``leech build`` does,
    without rendering its diagnostics."""
    session = session_mod.Session()
    exe = root.with_suffix("").absolute()
    built = None
    with common.reporting_user_errors(session):
        common.build(root, {toolchain.OutputKind.EXE: exe}, session)
        built = exe
    return BuildResult(built, session.diags.sorted())


def check_program(root: pathlib.Path) -> tuple[errors.UserError, ...]:
    """Check a program as ``leech check`` does, returning its diagnostics in source order."""
    session = session_mod.Session()
    with common.reporting_user_errors(session):
        leech_program.Program(root, entry=True).check(session)
    return session.diags.sorted()


def emit_error_while_checking(monkeypatch) -> None:
    """Make checking emit an error without raising it."""
    discover = mono.discover

    def discover_and_emit(mod: ir_module.Mod) -> mono.MonoResult:
        mod.ctx.diags.error(errors.CcNotFoundError("emitted"))
        return discover(mod)

    monkeypatch.setattr(mono, "discover", discover_and_emit)


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

    A module's path follows from its name, as the compiler requires: ``pkg::helper`` is
    ``pkg/helper.leech`` in the workspace. Only the final name segment must be non-reserved because
    intermediate segments represent package directories. Tests cannot supply modules named
    ``std::...``, which belong to the bundled standard library.
    """

    name: Final[str]
    src: Final[str]
    path: Final[pathlib.Path]

    def __init__(self, name: str, src: str) -> None:
        segments = parse.parse_qualified_name(name)
        if segments is None:
            raise ValueError(f"invalid qualified module name: {name!r}")
        if segments[0] == "std":
            raise ValueError("tests cannot supply bundled standard library modules")
        if reserved.is_reserved(segments[-1]):
            raise ValueError(f"reserved final module name segment: {segments[-1]!r}")

        self.name = name
        self.src = src
        self.path = pathlib.Path(*segments).with_suffix(".leech")

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
    """Compiled modules keyed by qualified name in declaration order, and their diagnostics."""

    mods: Final[Mapping[str, CompiledMod]]
    diags: Final[diag.Diags]

    def __init__(self, mods: Mapping[str, CompiledMod], diags: diag.Diags) -> None:
        self.mods = types.MappingProxyType(dict(mods))
        self.diags = diags

    def __repr__(self) -> str:
        return f"{type(self).__name__}(mods={tuple(self.mods)!r})"


class CompilerHarness:
    """Compiler test operations scoped to one temporary workspace.

    Each operation that compiles emits its diagnostics, apart from an error it raises, to the
    ``diags`` it is given, or to a new collection of its own. Pass ``diags`` to inspect them
    after the operation raises.
    """

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
        if path.is_relative_to(self.workspace / _LINK_DIR):
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

    def load(self, program: str | TestProgram) -> ir_module.Mod:
        """Load the root and its imports without checking anything, for inspecting IR lazily."""
        program = self._coerce_program(program)
        paths = self._materialize(program)
        ctx = compilation.Ctx()
        return ctx.loader.load_root(paths[program.root.name], program.root.name)

    def build(
        self, program: str | TestProgram, *, diags: Optional[diag.Diags] = None
    ) -> ir_module.Mod:
        """Build semantic IR for the root after materializing every supplied module.

        The diagnostics are the returned module's ``ctx.diags``.
        """
        program = self._coerce_program(program)
        paths = self._materialize(program)
        checked = leech_program.check_module(
            paths[program.root.name], _session(diags), qualified_name=program.root.name
        )
        return checked.mod

    def compile(
        self,
        program: str | TestProgram,
        *,
        entry: bool = False,
        diags: Optional[diag.Diags] = None,
    ) -> CompiledProgram:
        """Compile every supplied module independently under its declared name.

        With ``entry``, the root module's ``main`` becomes the program entry point. Every
        module's compilation emits to the same diagnostics.
        """
        session = _session(diags)
        program = self._coerce_program(program)
        src_paths = self._materialize(program)
        compiled = dict[str, CompiledMod]()
        for mod in (program.root, *program.mods):
            src_path = src_paths[mod.name]
            llvm_ir = leech_program.check_module(
                src_path, session, qualified_name=mod.name, entry=entry and mod is program.root
            ).llvm_ir()
            llvm_path = src_path.with_suffix(".ll")
            self._write_src(llvm_path, llvm_ir)
            compiled[mod.name] = CompiledMod(mod, src_path, llvm_path, llvm_ir)
        return CompiledProgram(compiled, session.diags)

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
        """Link the program and the bundled modules into a native executable, as leech does."""
        llvm_irs = [mod.llvm_ir for mod in compiled.mods.values()]
        llvm_irs.extend(_bundled_mod_llvm_ir().values())
        linked = ll_emit.link([ll_emit.parse(llvm_ir) for llvm_ir in llvm_irs])
        link_dir = self.workspace / _LINK_DIR
        link_dir.mkdir(exist_ok=True)
        obj_path = link_dir / "program.o"
        obj_path.write_bytes(ll_emit.emit(linked, ll_emit.EmitKind.OBJ, 0))
        exe_path = link_dir / "program"
        try:
            linker = toolchain.Linker.from_env()
        except errors.UserError as err:
            raise AssertionError(f"{err}; workspace: {self.workspace}") from err
        result = self._invoke_tool([*linker.command, str(obj_path), "-o", str(exe_path)])
        if result.returncode != 0:
            raise self._tool_failure("linking failed", result)
        return exe_path

    def run(
        self, program: str | TestProgram, *, diags: Optional[diag.Diags] = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a program entered through its root's ``main``, linking unshadowed bundled modules."""
        program = self._coerce_program(program)
        compiled = self.compile(program, entry=True, diags=diags)
        exe_path = self._link(compiled)
        return self._invoke_tool([str(exe_path)])

    def check(
        self,
        program: str | TestProgram,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_status: int = 0,
        diags: Optional[diag.Diags] = None,
    ) -> None:
        """Assert exact streams and status, defaulting to empty streams and success."""
        result = self.run(program, diags=diags)
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
        diags: Optional[diag.Diags] = None,
    ) -> None:
        """Assert a signal exit, exact stdout, and the stable prefix of LLVM's stderr."""
        result = self.run(program, diags=diags)
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
