import subprocess
from pathlib import Path

from leech import ast
from leech.main import compile_to_llvm_ir
from leech.parse import build_parser
from leech.src import SrcFile


def find_pos(src: str, substr: str) -> tuple[int, int]:
    """Return the 1-based (line, column) of the start of substr in src."""
    idx = src.index(substr)
    before = src[:idx]
    line = before.count("\n") + 1
    col = idx - before.rfind("\n")
    return line, col


def write_whole_file(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write(content)

    print(f"{path}:\n")
    print(content)


def compile_file(path: Path) -> Path:
    llir = compile_to_llvm_ir(SrcFile(path))
    llir_path = path.with_suffix(".ll")
    write_whole_file(llir_path, llir)
    return llir_path


def compile_str(tmp_path: Path, src: str) -> Path:
    path = tmp_path / "main.leech"
    write_whole_file(path, src)
    return compile_file(path)


def parse_mod(tmp_path: Path, src: str) -> ast.Mod:
    path = tmp_path / "main.leech"
    write_whole_file(path, src)
    file = SrcFile(path)
    tree = build_parser("mod").parse(file.src)
    return ast.Mod(file, tree)


def compile_modules(tmp_path: Path, **modules: str) -> list[Path]:
    for mod_name, mod_src in modules.items():
        write_whole_file(tmp_path / f"{mod_name}.leech", mod_src)

    return [compile_file(tmp_path / f"{mod_name}.leech") for mod_name in modules]


def check_prog_output(
    tmp_path: Path, src: str, expected_output, expected_exit_status, **modules: str
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
