from pathlib import Path
import subprocess

from l0.main import compile
from l0.src import SrcFile


def write_whole_file(path: Path, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def compile_file(path: Path) -> Path:
    llir = compile(SrcFile(path))
    llir_path = path.with_suffix(".ll")
    write_whole_file(llir_path, llir)
    return llir_path


def compile_str(tmp_path: Path, src: str) -> Path:
    path = tmp_path / "main.l0"
    write_whole_file(path, src)
    return compile_file(path)


def compile_modules(tmp_path: Path, **modules: str) -> list[Path]:
    for mod_name, mod_src in modules.items():
        write_whole_file(tmp_path / f"{mod_name}.l0", mod_src)

    return [compile_file(tmp_path / f"{mod_name}.l0") for mod_name in modules.keys()]


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
        universal_newlines=True,
    )
    assert proc.stdout == f"{expected_output}"
    assert proc.returncode == expected_exit_status
