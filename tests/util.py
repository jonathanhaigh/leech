# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import functools
import pathlib
import subprocess
from collections.abc import Sequence
from typing import Optional

from leech import ast, driver, ir_module, opt_util, parse
from leech import src as leech_src

_STD_ROOT = pathlib.Path(parse.__file__).parent / "std"
_PRELUDE_PATH = _STD_ROOT / "prelude.leech"


def find_pos(src: str, substr: str) -> tuple[int, int]:
    """Return the 1-based (line, column) of the start of substr in src."""
    idx = src.index(substr)
    before = src[:idx]
    line = before.count("\n") + 1
    col = idx - before.rfind("\n")
    return line, col


def write_whole_file(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(content)

    print(f"{path}:\n")
    print(content)


def compile_file(path: pathlib.Path, qualified_name: Optional[str] = None) -> pathlib.Path:
    llir = driver.compile_to_llvm_ir(leech_src.SrcFile(path), qualified_name)
    llir_path = path.with_suffix(".ll")
    write_whole_file(llir_path, llir)
    return llir_path


def compile_str(tmp_path: pathlib.Path, src: str) -> pathlib.Path:
    path = tmp_path / "main.leech"
    write_whole_file(path, src)
    return compile_file(path)


def parse_mod(tmp_path: pathlib.Path, src: str) -> ast.Mod:
    path = tmp_path / "main.leech"
    write_whole_file(path, src)
    file = leech_src.SrcFile(path)
    tree = parse.build_parser("mod").parse(file.src)
    return ast.Mod(file, tree)


def build_ir_mod(tmp_path: pathlib.Path, src: str) -> ir_module.Mod:
    """Parse and build ``src`` into IR, without lowering or compiling it.

    For exercising type-checking (or anything else that only needs a
    built :class:`~leech.ir_module.Mod`) on code that can't yet be
    lowered all the way to a runnable program.
    """
    path = tmp_path / "main.leech"
    write_whole_file(path, src)
    return driver.compile_to_ir(leech_src.SrcFile(path))


def compile_modules(
    tmp_path: pathlib.Path,
    qualified_names: Optional[dict[str, str]] = None,
    /,
    **modules: str,
) -> list[pathlib.Path]:
    """Write and compile each of ``modules`` as its own standalone root file.

    :param qualified_names: Per-module override for the qualified name
        :func:`compile_file` gives it, keyed by the same name used in
        ``modules``. Defaults (for any module not present here) to
        ``mod_name`` with ``/`` replaced by ``::`` - correct whenever
        ``mod_name`` is reached by exactly one import hop directly from
        another module in this same fixture, whose own ``import`` names
        exactly ``mod_name``'s path. That default is *wrong* for a module
        reached only transitively (e.g. ``pkg/a.leech`` importing
        ``sub::helper``, found at ``pkg/sub/helper.leech``): the real
        compiler gives it the qualified name ``sub::helper`` (from ``a``'s
        own two-segment import text, resolved relative to ``a``'s
        directory), not ``pkg::sub::helper`` (the fixture path relative to
        ``tmp_path``) - such a case must pass its qualified name here
        explicitly so this standalone recompilation agrees with it.
    """
    qualified_names = opt_util.opt_or_default(qualified_names, {})
    for mod_name, mod_src in modules.items():
        write_whole_file(tmp_path / f"{mod_name}.leech", mod_src)

    return [
        compile_file(
            tmp_path / f"{mod_name}.leech",
            qualified_name=qualified_names.get(mod_name, mod_name.replace("/", "::")),
        )
        for mod_name in modules
    ]


@functools.cache
def _prelude_llvm_ir() -> str:
    """The compiled prelude module's LLVM IR, computed once per test run.

    A real compilation only ever *declares* an imported module's items
    (see :meth:`~leech.codegen.Compiler.compile`'s docstring) - the
    prelude module, though always loaded, is no exception, so its
    ``panic``/``assert`` bodies need to be compiled (as their own root,
    the same way :func:`compile_modules` compiles every sibling test
    fixture module standalone) and linked in separately for a program
    that actually calls them to run.
    """
    return driver.compile_to_llvm_ir(leech_src.SrcFile(_PRELUDE_PATH), "prelude")


@functools.cache
def _bundled_std_llvm_ir(mod_name: str) -> str:
    """The compiled LLVM IR of the real bundled ``std::{mod_name}``, once per
    test run.

    Same rationale as :func:`_prelude_llvm_ir`: any test whose fixture
    ``import``s a real ``std::...`` module (resolved via
    :meth:`~leech.ir_loader.ModLoader.resolve_import`'s bundled-root search
    path, not a ``tmp_path`` fixture) needs that module's body compiled and
    linked in too, under the same qualified name real import resolution
    would give it (``"std::{mod_name}"``, from the two-segment ``std::...``
    import path).
    """
    path = _STD_ROOT / f"{mod_name}.leech"
    return driver.compile_to_llvm_ir(leech_src.SrcFile(path), f"std::{mod_name}")


def link_and_run(
    tmp_path: pathlib.Path,
    llir_mod_paths: Sequence[pathlib.Path],
    std_modules: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    """Link ``llir_mod_paths`` (plus the prelude and any ``std_modules``)
    into one program and run it, returning the completed process.

    :param std_modules: Names (relative to ``std/``, e.g. ``"io"`` for
        ``std::io``) of real bundled standard-library modules the linked
        program imports and that therefore need their own compiled body
        linked in too - the prelude module is always included regardless,
        since it's always loaded.
    """
    prelude_llir_path = tmp_path / "prelude.ll"
    write_whole_file(prelude_llir_path, _prelude_llvm_ir())
    all_llir_paths = [*llir_mod_paths, prelude_llir_path]

    for mod_name in std_modules:
        std_llir_path = tmp_path / f"std_{mod_name}.ll"
        write_whole_file(std_llir_path, _bundled_std_llvm_ir(mod_name))
        all_llir_paths.append(std_llir_path)

    llir_path = tmp_path / "exe.bc"
    subprocess.run(["llvm-link", "-o", llir_path, *all_llir_paths], check=True)

    return subprocess.run(
        ["lli", llir_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def check_prog_output(
    tmp_path: pathlib.Path,
    src: str,
    expected_output,
    expected_exit_status,
    *,
    std_modules: Sequence[str] = (),
    **modules: str,
) -> None:
    """Compile ``src`` (as ``main``) plus every module in ``modules``, link,
    and run, asserting the program's captured output and exit status.

    :param std_modules: See :func:`link_and_run`.
    """
    modules["main"] = src
    llir_mod_paths = compile_modules(tmp_path, **modules)
    proc = link_and_run(tmp_path, llir_mod_paths, std_modules)

    if expected_exit_status < 0:
        # A negative expected_exit_status means the process is expected to
        # be killed by a signal (e.g. -signal.SIGABRT for an abort()) - lli
        # itself installs a SIGABRT handler that prints a large, address-
        # dependent crash backtrace to stderr (merged into proc.stdout
        # above) whenever *any* code running under it aborts, including an
        # ordinary, intentional one from JIT'd Leech code, not just an
        # internal lli crash. That backtrace can't be matched exactly, so
        # only the expected prefix - whatever the program itself wrote
        # before aborting - is checked here.
        assert proc.stdout.startswith(f"{expected_output}")
    else:
        assert proc.stdout == f"{expected_output}"
    assert proc.returncode == expected_exit_status
