# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import importlib.metadata
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from leech import driver, errors, target
from tests import harness


def run_cli(*args, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leechc", *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def run_tool(*command) -> subprocess.CompletedProcess:
    """Run an external tool, failing clearly if it is missing or exits unsuccessfully."""
    tool = str(command[0])
    assert shutil.which(tool) is not None, f"this test needs {tool!r} on PATH"
    proc = subprocess.run(
        [str(arg) for arg in command], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"{tool} failed ({proc.returncode}):\n{proc.stderr}"
    return proc


def run_leechc_in_process(monkeypatch, *args) -> int:
    monkeypatch.setattr(sys, "argv", ["leechc", *(str(a) for a in args)])
    with harness.isolated_diagnostics(), pytest.raises(SystemExit) as exc_info:
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
    ("filename", "module_name", "entry", "expected_main_symbol"),
    (
        ("main.leech", None, False, "main::main"),
        ("app.leech", None, True, "app::main"),
        ("app.leech", "main", True, "main::main"),
    ),
)
def test_cli_entry_flag_controls_c_main(
    tmp_path, filename, module_name, entry, expected_main_symbol
):
    src_path = tmp_path / filename
    src_path.write_text("pub fn main() i32 { return 0; }\n")
    out_path = tmp_path / f"{src_path.stem}.ll"
    args = [src_path, "-o", out_path]
    if module_name is not None:
        args.extend(("--module-name", module_name))
    if entry:
        args.append("--entry")

    proc = run_cli(*args)

    assert proc.returncode == errors.NOTE
    llvm_ir = out_path.read_text()
    assert f'define i32 @"{expected_main_symbol}"()' in llvm_ir
    assert ('define i32 @"main"()' in llvm_ir) == entry


def test_cli_entry_without_main_reports_error(tmp_path):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn other() i32 { return 0; }\n")

    proc = run_cli(src_path, "--entry")

    assert proc.returncode == errors.ERROR
    assert proc.stderr == f'ERROR: Entry module "app" ({src_path}) has no "main" function\n'
    assert not src_path.with_suffix(".ll").exists()


def test_cli_compiles_linkable_std_io_program(tmp_path):
    main_path = tmp_path / "app.leech"
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
        (main_path, ("--entry",), tmp_path / "app.ll"),
        (std_root / "io.leech", ("--module-name", "std::io"), tmp_path / "io.ll"),
        (std_root / "prelude.leech", ("--module-name", "prelude"), tmp_path / "prelude.ll"),
    )

    for src_path, extra_args, out_path in modules:
        proc = run_cli(src_path, *extra_args, "-o", out_path)
        assert proc.returncode == errors.NOTE
        assert proc.stdout == ""
        assert proc.stderr == ""

    bitcode_path = tmp_path / "program.bc"
    run_tool("llvm-link", *(out_path for _, _, out_path in modules), "-o", bitcode_path)
    proc = subprocess.run(
        ["lli", bitcode_path],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert proc.stdout == "hello\n"
    assert proc.stderr == ""


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


@pytest.mark.parametrize(
    ("emit", "suffix", "magic"),
    (
        ("llvm-ir", ".ll", b"; ModuleID"),
        ("llvm-bc", ".bc", b"BC\xc0\xde"),
        ("asm", ".s", b"\t.file"),
        ("obj", ".o", b"\x7fELF"),
    ),
)
def test_cli_emit_derives_output_suffix(tmp_path, emit, suffix, magic):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")

    proc = run_cli(src_path, "--emit", emit)

    assert proc.returncode == errors.NOTE
    assert proc.stderr == ""
    assert src_path.with_suffix(suffix).read_bytes().startswith(magic)


def test_cli_optimization_level_changes_llvm_ir(tmp_path):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn answer() i32 { let x = 40; return x + 2; }\n")
    unoptimized_path = tmp_path / "o0.ll"
    optimized_path = tmp_path / "o2.ll"

    assert run_cli(src_path, "-o", unoptimized_path).returncode == errors.NOTE
    assert run_cli(src_path, "-O2", "-o", optimized_path).returncode == errors.NOTE

    assert "alloca" in unoptimized_path.read_text()
    optimized = optimized_path.read_text()
    assert "alloca" not in optimized
    assert "ret i32 42" in optimized


def test_cli_rejects_unsupported_optimization_level(tmp_path):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")

    proc = run_cli(src_path, "-O4")

    assert proc.returncode == 2
    assert "argument -O: invalid choice: '4'" in proc.stderr
    assert not src_path.with_suffix(".ll").exists()


def test_cli_object_files_link_into_position_independent_executable(tmp_path):
    app_path = tmp_path / "app.leech"
    app_path.write_text('import std::io;\nfn main() i32 { io::println("linked"); return 3; }\n')
    std_root = pathlib.Path(driver.__file__).parent / "std"
    modules = (
        (app_path, ("--entry",)),
        (std_root / "io.leech", ("--module-name", "std::io")),
        (std_root / "prelude.leech", ("--module-name", "prelude")),
    )
    obj_paths = []
    for src_path, extra_args in modules:
        obj_path = tmp_path / f"{src_path.stem}.o"
        proc = run_cli(src_path, *extra_args, "--emit", "obj", "-O1", "-o", obj_path)
        assert proc.returncode == errors.NOTE, proc.stderr
        obj_paths.append(obj_path)

    exe_path = tmp_path / "app"
    run_tool("cc", *obj_paths, "-o", exe_path)
    proc = subprocess.run([exe_path], capture_output=True, text=True, check=False)

    assert proc.returncode == 3
    assert proc.stdout == "linked\n"
    assert proc.stderr == ""


@pytest.mark.parametrize("filename", ("main.ll", "main", "main.leech.txt"))
@pytest.mark.parametrize("explicit_o", (False, True))
def test_cli_rejects_source_without_leech_suffix(tmp_path, filename, explicit_o):
    src_path = tmp_path / filename
    src = "pub fn answer() i32 { return 42; }\n"
    src_path.write_text(src)
    out_path = tmp_path / "out.ll"
    args = [src_path, "-o", out_path] if explicit_o else [src_path]

    proc = run_cli(*args)

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.startswith("usage: leechc ")
    assert f"source file name must end in '.leech': {str(src_path)!r}\n" in proc.stderr
    assert src_path.read_text() == src
    assert not out_path.exists()


def test_cli_names_source_file_in_llvm_ir_and_assembly(tmp_path):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")

    assert run_cli(src_path).returncode == errors.NOTE
    assert run_cli(src_path, "--emit", "asm").returncode == errors.NOTE

    llvm_ir = src_path.with_suffix(".ll").read_text()
    assert llvm_ir.startswith(f'; ModuleID = "app"\nsource_filename = "{src_path}"\n')
    # Like clang, LLVM names only the source file's base name in the assembly.
    assert '\t.file\t"app.leech"\n' in src_path.with_suffix(".s").read_text()


def test_cli_escapes_source_filename(tmp_path):
    src_dir = tmp_path / 'we"ird\\dir'
    src_dir.mkdir()
    src_path = src_dir / "app.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")

    proc = run_cli(src_path, "--emit", "obj")

    assert proc.returncode == errors.NOTE, proc.stderr
    escaped = str(src_path).replace("\\", "\\5C").replace('"', "\\22")
    llvm_ir_path = tmp_path / "app.ll"
    assert run_cli(src_path, "-o", llvm_ir_path).returncode == errors.NOTE
    assert f'source_filename = "{escaped}"\n' in llvm_ir_path.read_text()


@pytest.mark.usefixtures("isolated_diagnostics")
def test_resolve_import_paths_orders_cli_paths_before_environment(tmp_path, monkeypatch):
    dirs = [tmp_path / name for name in ("cli", "env1", "env2")]
    for path in dirs:
        path.mkdir()
    monkeypatch.chdir(tmp_path)
    environ = {driver.IMPORT_PATH_ENV_VAR: f"{dirs[1]}{os.pathsep}{os.pathsep}env2"}

    roots = driver.resolve_import_paths([pathlib.Path("cli")], environ)

    assert roots == tuple(dirs)
    assert errors.all_errors() == []


@pytest.mark.usefixtures("isolated_diagnostics")
def test_resolve_import_paths_warns_once_per_missing_path(tmp_path):
    missing = tmp_path / "missing"
    not_dir = tmp_path / "file.txt"
    not_dir.write_text("")
    (tmp_path / "existing").mkdir()
    environ = {driver.IMPORT_PATH_ENV_VAR: str(tmp_path / "existing" / ".." / "missing")}

    roots = driver.resolve_import_paths([missing, not_dir], environ)

    assert roots == ()
    assert [str(w) for w in errors.all_errors()] == [
        f'Import path "{missing}" (from the command line) is not a directory',
        f'Import path "{not_dir}" (from the command line) is not a directory',
    ]
    assert errors.error_level() == errors.WARNING


def _write_lib_program(tmp_path) -> tuple[pathlib.Path, pathlib.Path]:
    src_path = tmp_path / "app" / "main.leech"
    src_path.parent.mkdir()
    src_path.write_text("import lib; pub fn f() i32 { return lib::g(); }\n")
    lib_dir = tmp_path / "libs"
    lib_dir.mkdir()
    (lib_dir / "lib.leech").write_text("pub fn g() i32 { return 1; }\n")
    return src_path, lib_dir


@pytest.mark.parametrize("option", ("-I", "--import-path"))
def test_cli_import_path_option_resolves_imports(tmp_path, option):
    src_path, lib_dir = _write_lib_program(tmp_path)

    proc = run_cli(src_path, option, lib_dir)

    assert proc.returncode == errors.NOTE, proc.stderr
    assert 'call i32 @"lib::g"()' in src_path.with_suffix(".ll").read_text()


def test_cli_attached_import_path_resolves_imports(tmp_path):
    src_path, lib_dir = _write_lib_program(tmp_path)

    proc = run_cli(src_path, f"-I{lib_dir}")

    assert proc.returncode == errors.NOTE, proc.stderr


def test_cli_rejects_abbreviated_long_options(tmp_path):
    src_path, lib_dir = _write_lib_program(tmp_path)

    proc = run_cli(src_path, "--import", lib_dir)

    assert proc.returncode == 2
    assert "unrecognized arguments: --import" in proc.stderr


def test_cli_import_path_environment_variable_resolves_imports(tmp_path):
    src_path, lib_dir = _write_lib_program(tmp_path)
    env = {**os.environ, driver.IMPORT_PATH_ENV_VAR: str(lib_dir)}

    proc = run_cli(src_path, env=env)

    assert proc.returncode == errors.NOTE, proc.stderr
    assert 'call i32 @"lib::g"()' in src_path.with_suffix(".ll").read_text()


def test_cli_missing_import_path_warns_and_still_compiles(tmp_path):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn answer() i32 { return 42; }\n")
    missing = tmp_path / "missing"

    proc = run_cli(src_path, "-I", missing)

    assert proc.returncode == errors.WARNING
    assert proc.stderr == (
        f'WARNING: Import path "{missing}" (from the command line) is not a directory\n'
    )
    assert src_path.with_suffix(".ll").exists()
