# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
import subprocess

from leech import ast, driver, ir_module, parse
from leech import src as leech_src


def find_pos(src: str, substr: str) -> tuple[int, int]:
    """Return the 1-based (line, column) of the start of substr in src."""
    idx = src.index(substr)
    before = src[:idx]
    line = before.count("\n") + 1
    col = idx - before.rfind("\n")
    return line, col


def write_whole_file(path: pathlib.Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(content)

    print(f"{path}:\n")
    print(content)


def compile_file(path: pathlib.Path) -> pathlib.Path:
    llir = driver.compile_to_llvm_ir(leech_src.SrcFile(path))
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


def compile_modules(tmp_path: pathlib.Path, **modules: str) -> list[pathlib.Path]:
    for mod_name, mod_src in modules.items():
        write_whole_file(tmp_path / f"{mod_name}.leech", mod_src)

    return [compile_file(tmp_path / f"{mod_name}.leech") for mod_name in modules]


def check_prog_output(
    tmp_path: pathlib.Path, src: str, expected_output, expected_exit_status, **modules: str
) -> None:
    modules["main"] = src
    llir_mod_paths = compile_modules(tmp_path, **modules)

    llir_path = tmp_path / "exe.bc"
    subprocess.run(["llvm-link", "-o", llir_path, *llir_mod_paths], check=True)

    proc = subprocess.run(
        ["lli", llir_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    assert proc.stdout == f"{expected_output}"
    assert proc.returncode == expected_exit_status
