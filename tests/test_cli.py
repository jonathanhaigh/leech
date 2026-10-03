# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import importlib.metadata
import pathlib
import subprocess
import sys

import pytest

from leech import driver, errors, target


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leechc", *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
    )


def run_leechc_in_process(monkeypatch, *args) -> int:
    monkeypatch.setattr(errors, "_errors", [])
    monkeypatch.setattr(errors, "_error_level", errors.NOTE)
    monkeypatch.setattr(sys, "argv", ["leechc", *(str(a) for a in args)])
    with pytest.raises(SystemExit) as exc_info:
        driver.main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code


def test_cli_success_infers_output_path(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 42;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.NOTE
    assert proc.stdout == ""
    assert proc.stderr == ""
    ll_path = src_path.with_suffix(".ll")
    assert ll_path.exists()
    assert "ret i32 42" in ll_path.read_text()


def test_cli_success_with_explicit_o(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0;
}
""")
    out_path = tmp_path / "custom_out.ll"

    proc = run_cli(src_path, "-o", out_path)

    assert proc.returncode == errors.NOTE
    assert out_path.exists()


def test_cli_defaults_module_name_to_source_stem(tmp_path):
    src_path = tmp_path / "array.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")

    proc = run_cli(src_path)

    assert proc.returncode == errors.NOTE
    assert 'define i32 @"array::answer"' in src_path.with_suffix(".ll").read_text()


def test_cli_accepts_explicit_qualified_module_name(tmp_path):
    src_path = tmp_path / "math.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")
    out_path = tmp_path / "math.ll"

    proc = run_cli(src_path, "--module-name", "array::math", "-o", out_path)

    assert proc.returncode == errors.NOTE
    assert 'define i32 @"array::math::answer"' in out_path.read_text()


@pytest.mark.parametrize(
    "module_name",
    ("", "::math", "pkg::", "pkg::::math", "pkg-name", "1pkg", "fn", "array", "pkg::i32"),
)
def test_cli_rejects_invalid_module_name(tmp_path, module_name, monkeypatch, capsys):
    src_path = tmp_path / "math.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")
    out_path = tmp_path / "math.ll"

    code = run_leechc_in_process(
        monkeypatch, src_path, "--module-name", module_name, "-o", out_path
    )

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("usage: leechc ")
    assert "argument --module-name: invalid qualified module name" in captured.err
    assert not out_path.exists()


@pytest.mark.parametrize(
    ("filename", "module_name", "expected_symbol"),
    (
        ("main.leech", None, 'define i32 @"main"'),
        ("app.leech", None, 'define i32 @"app::main"'),
        ("app.leech", "main", 'define i32 @"main"'),
    ),
)
def test_cli_module_name_controls_entry_symbol(tmp_path, filename, module_name, expected_symbol):
    src_path = tmp_path / filename
    src_path.write_text("pub fn main() i32 { return 0; }\n")
    out_path = tmp_path / f"{src_path.stem}.ll"
    args = [src_path, "-o", out_path]
    if module_name is not None:
        args.extend(("--module-name", module_name))

    proc = run_cli(*args)

    assert proc.returncode == errors.NOTE
    assert expected_symbol in out_path.read_text()


def test_cli_compiles_linkable_std_io_program(tmp_path):
    main_path = tmp_path / "main.leech"
    main_path.write_text(
        """import std::io;
pub fn main() i32 {
    io::println("hello");
    return 0;
}
"""
    )
    std_root = pathlib.Path(driver.__file__).parent / "std"
    modules = (
        (main_path, "main", tmp_path / "main.ll"),
        (std_root / "io.leech", "std::io", tmp_path / "io.ll"),
        (std_root / "prelude.leech", "prelude", tmp_path / "prelude.ll"),
    )

    for src_path, module_name, out_path in modules:
        proc = run_cli(src_path, "--module-name", module_name, "-o", out_path)
        assert proc.returncode == errors.NOTE
        assert proc.stdout == ""
        assert proc.stderr == ""

    bitcode_path = tmp_path / "program.bc"
    subprocess.run(
        ["llvm-link", *(out_path for _, _, out_path in modules), "-o", bitcode_path],
        check=True,
    )
    proc = subprocess.run(
        ["lli", bitcode_path],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert proc.stdout == "hello\n"
    assert proc.stderr == ""


def test_cli_ll_suffix_requires_explicit_o(tmp_path):
    src_path = tmp_path / "main.ll"
    src_path.write_text("""pub fn main() i32 {
    return 0;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr == "-o option must be given if source file name ends in '.ll'\n"


def test_cli_error_renders_message_and_does_not_write_output(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return true + 1;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    assert proc.stderr == (
        'ERROR: Left operand of binary operation "+" has invalid type "bool", '
        'expecting "an integer type"\n'
        "2|     return true + 1;\n"
        "--------------^\n"
        'NOTE: For "+" operation here\n'
        "2|     return true + 1;\n"
        "-------------------^\n"
    )
    assert not src_path.with_suffix(".ll").exists()


def test_cli_warning_still_writes_output(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 1;
    return 2;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.WARNING
    assert proc.stdout == ""
    assert proc.stderr == ("WARNING: return statement is unreachable\n3|     return 2;\n-------^\n")
    ll_path = src_path.with_suffix(".ll")
    assert ll_path.exists()
    assert "ret i32 1" in ll_path.read_text()


def test_cli_unexpected_character_error(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0 @ 1;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    assert proc.stderr == (
        'ERROR: Unexpected character "@"\n2|     return 0 @ 1;\n----------------^\n'
    )
    assert not src_path.with_suffix(".ll").exists()


def test_cli_unexpected_token_error(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    assert proc.stderr == ('ERROR: Unexpected token "}"\n3| }\n---^\nNOTE: Expected one of: ";"\n')
    assert not src_path.with_suffix(".ll").exists()


def test_cli_unexpected_end_of_input_error(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 0;
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.ERROR
    assert proc.stdout == ""
    # The exact set of expected tokens is an implementation detail of the
    # grammar (e.g. it grows whenever a new prefix operator is added), so
    # only the diagnostic's kind and source position are asserted exactly.
    assert proc.stderr.startswith(
        "ERROR: Unexpected end of input\n"
        "2|     return 0;\n"
        "---------------^\n"
        "NOTE: Expected one of: "
    )
    assert not src_path.with_suffix(".ll").exists()


def test_cli_warning_fires_once_for_multiple_dead_statements(tmp_path):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 1;
    let y = 2;
    return y;
}
""")

    proc = run_cli(src_path)

    assert proc.returncode == errors.WARNING
    assert proc.stdout == ""
    assert proc.stderr == ("WARNING: let statement is unreachable\n3|     let y = 2;\n-------^\n")


def test_run_in_process_infers_output_path(tmp_path, monkeypatch, capsys):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return 42;
}
""")

    code = run_leechc_in_process(monkeypatch, src_path)

    assert code == errors.NOTE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    ll_path = src_path.with_suffix(".ll")
    assert ll_path.exists()
    assert "ret i32 42" in ll_path.read_text()


def test_run_in_process_ll_suffix_requires_explicit_o(tmp_path, monkeypatch, capsys):
    src_path = tmp_path / "main.ll"
    src_path.write_text("""pub fn main() i32 {
    return 0;
}
""")

    code = run_leechc_in_process(monkeypatch, src_path)

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "-o option must be given if source file name ends in '.ll'\n"


def test_run_in_process_error_renders_message_and_skips_output(tmp_path, monkeypatch, capsys):
    src_path = tmp_path / "main.leech"
    src_path.write_text("""pub fn main() i32 {
    return true + 1;
}
""")

    code = run_leechc_in_process(monkeypatch, src_path)

    assert code == errors.ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        'ERROR: Left operand of binary operation "+" has invalid type "bool", '
        'expecting "an integer type"\n'
        "2|     return true + 1;\n"
        "--------------^\n"
        'NOTE: For "+" operation here\n'
        "2|     return true + 1;\n"
        "-------------------^\n"
    )
    assert not src_path.with_suffix(".ll").exists()


def test_cli_version_reports_package_metadata():
    proc = run_cli("--version")

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert proc.stdout.startswith(f"leechc {importlib.metadata.version('leech')} (LLVM ")
    assert proc.stdout.endswith(f"; target {target.TRIPLE})\n")
