from pathlib import Path
import subprocess

from l0.main import compile
from l0.src import SrcFile


def compile_str(tmp_path: Path, src: str) -> str:
    path = tmp_path / "main.l0"
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)

    return compile(SrcFile(path))


def check_prog_output(
    tmp_path: Path, src: str, expected_output, expected_exit_status
) -> None:
    ir = compile_str(tmp_path, src)
    proc = subprocess.run(
        ["lli"],
        input=ir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    assert proc.stdout == f"{expected_output}"
    assert proc.returncode == expected_exit_status
