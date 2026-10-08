# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import importlib.metadata
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

from leech import errors, program, session, target, toolchain
from leech.cli import leech as leech_cli
from tests import harness

_HELLO = 'import std::io;\npub fn main() i32 { io::println("hello"); return 0; }\n'


def run_build(cwd: pathlib.Path, *args, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["leech", "build", *(str(a) for a in args)],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env=env,
    )


def run_build_in_process(monkeypatch, cwd: pathlib.Path, *args) -> int:
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["leech", "build", *(str(a) for a in args)])
    with pytest.raises(SystemExit) as exc_info:
        leech_cli.main()
    code = exc_info.value.code
    assert isinstance(code, int)
    return code


def run_tool(*command) -> subprocess.CompletedProcess:
    """Run an external tool, failing clearly if it is missing or exits unsuccessfully."""
    tool = str(command[0])
    assert shutil.which(tool) is not None, f"this test needs {tool!r} on PATH"
    proc = subprocess.run(
        [str(arg) for arg in command], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"{tool} failed ({proc.returncode}):\n{proc.stderr}"
    return proc


def write(path: pathlib.Path, text: str) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def listing(directory: pathlib.Path) -> set[str]:
    return {path.relative_to(directory).as_posix() for path in directory.rglob("*")}


def env_with_cc(value: str) -> dict[str, str]:
    return {**os.environ, "CC": value}


def fn_definition(llvm_ir: str, symbol: str) -> str:
    match = re.search(rf'^define [^\n]*@"{re.escape(symbol)}"\(.*?^}}$', llvm_ir, re.M | re.S)
    assert match is not None, f"no definition of {symbol!r} in:\n{llvm_ir}"
    return match.group(0)


def test_build_writes_only_its_executable_to_the_current_directory(tmp_path):
    root = write(tmp_path / "src" / "app.leech", _HELLO)
    work = tmp_path / "work"
    work.mkdir()

    proc = run_build(work, root)

    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert listing(work) == {"app"}
    assert listing(root.parent) == {"app.leech"}
    assert run_tool(work / "app").stdout == "hello\n"


@pytest.mark.parametrize(
    ("emit", "name", "magic"),
    (
        ("exe", "app", b"\x7fELF"),
        ("obj", "app.o", b"\x7fELF"),
        ("asm", "app.s", b"\t.file"),
        ("llvm-ir", "app.ll", b"; ModuleID = 'app'\n"),
        ("llvm-bc", "app.bc", b"BC\xc0\xde"),
    ),
)
@pytest.mark.parametrize("explicit_o", (False, True))
def test_emit_kind_writes_one_output(tmp_path, emit, name, magic, explicit_o):
    root = write(tmp_path / "app.leech", _HELLO)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    args = [root, "--emit", emit]
    if explicit_o:
        args.extend(("-o", out_dir / "custom"))

    proc = run_build(tmp_path, *args)

    assert (proc.returncode, proc.stderr) == (0, "")
    if explicit_o:
        assert listing(tmp_path) == {"app.leech", "out", "out/custom"}
        assert (out_dir / "custom").read_bytes().startswith(magic)
    else:
        assert listing(tmp_path) == {"app.leech", "out", name}
        assert (tmp_path / name).read_bytes().startswith(magic)


def test_emit_llvm_ir_and_exe_writes_the_programs_ir_and_executable(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, "--emit", "llvm-ir,exe")

    assert (proc.returncode, proc.stderr) == (0, "")
    llvm_ir = (tmp_path / "app.ll").read_text()
    assert 'define i32 @"app::main"()' in llvm_ir
    assert 'define void @"std::io::println"(' in llvm_ir
    assert run_tool(tmp_path / "app").stdout == "hello\n"


def test_repeated_emit_kind_is_written_once(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, "--emit", "llvm-ir,llvm-ir", "-o", tmp_path / "out.ll")

    assert (proc.returncode, proc.stderr) == (0, "")
    assert listing(tmp_path) == {"app.leech", "out.ll"}


@pytest.mark.parametrize(
    ("args", "message"),
    (
        (("--emit", "llvm-ir,obj", "-o", "out"), "-o is only accepted with one --emit kind"),
        (("--emit", "exe,elf"), "argument --emit: invalid kind 'elf' (choose from llvm-ir, "),
        (("--emit", "exe,"), "argument --emit: invalid kind ''"),
    ),
)
def test_invalid_emit_is_a_usage_error(tmp_path, args, message):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, *args)

    assert proc.returncode == 2
    assert proc.stderr.startswith("usage: leech build ")
    assert message in proc.stderr
    assert listing(tmp_path) == {"app.leech"}


def test_executable_is_linked_from_the_emitted_object(tmp_path, monkeypatch):
    root = write(tmp_path / "app.leech", _HELLO)
    linked_objs = []
    link = toolchain.Linker.link

    def recording_link(linker, obj, exe):
        linked_objs.append(obj.read_bytes())
        link(linker, obj, exe)

    monkeypatch.setattr(toolchain.Linker, "link", recording_link)

    assert run_build_in_process(monkeypatch, tmp_path, root, "--emit", "obj,exe") == 0

    assert linked_objs == [(tmp_path / "app.o").read_bytes()]
    assert run_tool(tmp_path / "app").stdout == "hello\n"


def test_intermediates_go_to_a_removed_temporary_directory(tmp_path, monkeypatch):
    root = write(tmp_path / "app.leech", _HELLO)
    tmp_dirs = []

    class RecordingTemporaryDirectory(tempfile.TemporaryDirectory):
        def __enter__(self) -> str:
            tmp_dirs.append(pathlib.Path(self.name))
            return super().__enter__()

    monkeypatch.setattr(tempfile, "TemporaryDirectory", RecordingTemporaryDirectory)

    assert run_build_in_process(monkeypatch, tmp_path, root) == 0

    assert len(tmp_dirs) == 1
    assert not tmp_dirs[0].exists()
    assert listing(tmp_path) == {"app.leech", "app"}


def test_failed_check_writes_nothing(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 {\n    return true + 1;\n}\n")

    proc = run_build(tmp_path, root, "--emit", "llvm-ir,obj,exe")

    assert proc.returncode == 1
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
    assert listing(tmp_path) == {"main.leech"}


def test_failed_link_leaves_existing_outputs_unchanged(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)
    old = {
        name: write(tmp_path / name, f"old {name}").read_bytes()
        for name in ("app.ll", "app.o", "app")
    }

    proc = run_build(tmp_path, root, "--emit", "llvm-ir,obj,exe", env=env_with_cc("false"))

    assert proc.returncode == 1
    assert proc.stderr.startswith("ERROR: Linking failed: `false ")
    assert {name: (tmp_path / name).read_bytes() for name in old} == old
    assert listing(tmp_path) == {"app.leech", *old}


@pytest.mark.parametrize(
    ("emit", "name"),
    (("llvm-ir", "app.ll"), ("llvm-bc", "app.bc"), ("asm", "app.s"), ("obj", "app.o")),
)
def test_outputs_other_than_an_executable_need_no_c_compiler(tmp_path, emit, name):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, "--emit", emit, env=env_with_cc("/nonexistent/cc"))

    assert (proc.returncode, proc.stderr) == (0, "")
    assert listing(tmp_path) == {"app.leech", name}


def test_executable_needs_a_c_compiler(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, "--emit", "llvm-ir,exe", env=env_with_cc("/nonexistent/cc"))

    assert proc.returncode == 1
    assert proc.stderr.startswith('ERROR: C compiler "/nonexistent/cc" not found')
    assert listing(tmp_path) == {"app.leech"}


def test_warning_still_writes_output(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 {\n    return 1;\n    return 2;\n}\n")

    proc = run_build(tmp_path, root, "--emit", "llvm-ir")

    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == "WARNING: return statement is unreachable\n3|     return 2;\n-------^\n"
    assert "ret i32 1" in fn_definition((tmp_path / "main.ll").read_text(), "main::main")


def test_unexpected_character_error(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 {\n    return 0 @ 1;\n}\n")

    proc = run_build(tmp_path, root, "--emit", "llvm-ir")

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == (
        'ERROR: Unexpected character "@"\n2|     return 0 @ 1;\n----------------^\n'
    )
    assert listing(tmp_path) == {"main.leech"}


def test_unexpected_token_error(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 {\n    return 0\n}\n")

    proc = run_build(tmp_path, root, "--emit", "llvm-ir")

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == ('ERROR: Unexpected token "}"\n3| }\n---^\nNOTE: Expected one of: ";"\n')
    assert listing(tmp_path) == {"main.leech"}


def test_unexpected_end_of_input_error(tmp_path):
    root = write(tmp_path / "main.leech", "pub fn main() i32 {\n    return 0;\n")

    proc = run_build(tmp_path, root, "--emit", "llvm-ir")

    assert proc.returncode == 1
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
    assert listing(tmp_path) == {"main.leech"}


def test_warning_fires_once_for_multiple_dead_statements(tmp_path):
    root = write(
        tmp_path / "main.leech",
        "pub fn main() i32 {\n    return 1;\n    let y = 2;\n    return y;\n}\n",
    )

    proc = run_build(tmp_path, root, "--emit", "llvm-ir")

    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == "WARNING: let statement is unreachable\n3|     let y = 2;\n-------^\n"


def test_in_process_error_renders_message_and_writes_nothing(tmp_path, monkeypatch, capsys):
    root = write(tmp_path / "main.leech", "pub fn main() i32 {\n    return true + 1;\n}\n")

    code = run_build_in_process(monkeypatch, tmp_path, root)

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(
        'ERROR: Left operand of binary operation "+" has invalid type "bool", '
    )
    assert listing(tmp_path) == {"main.leech"}


def test_version_reports_package_metadata():
    proc = run_tool("leech", "--version")

    assert proc.stderr == ""
    assert proc.stdout.startswith(f"leech {importlib.metadata.version('leech')} (LLVM ")
    assert proc.stdout.endswith(f"; target {target.TRIPLE})\n")


def test_optimization_level_changes_llvm_ir(tmp_path):
    root = write(
        tmp_path / "app.leech",
        "pub fn answer() i32 { let x = 40; return x + 2; }\n"
        "pub fn main() i32 { return answer(); }\n",
    )

    assert run_build(tmp_path, root, "--emit", "llvm-ir", "-o", "o0.ll").returncode == 0
    assert run_build(tmp_path, root, "--emit", "llvm-ir", "-O2", "-o", "o2.ll").returncode == 0

    unoptimized = fn_definition((tmp_path / "o0.ll").read_text(), "app::answer")
    optimized = fn_definition((tmp_path / "o2.ll").read_text(), "app::answer")
    assert "alloca" in unoptimized
    assert "alloca" not in optimized
    assert "ret i32 42" in optimized


def test_rejects_unsupported_optimization_level(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, "-O4")

    assert proc.returncode == 2
    assert "argument -O: invalid choice: '4'" in proc.stderr
    assert listing(tmp_path) == {"app.leech"}


def test_object_file_links_into_position_independent_executable(tmp_path):
    root = write(
        tmp_path / "app.leech",
        'import std::io;\nfn main() i32 { io::println("linked"); return 3; }\n',
    )

    proc = run_build(tmp_path, root, "--emit", "obj", "-O1")

    assert (proc.returncode, proc.stderr) == (0, "")
    run_tool("cc", tmp_path / "app.o", "-o", tmp_path / "linked")
    result = subprocess.run([tmp_path / "linked"], capture_output=True, text=True, check=False)
    assert (result.returncode, result.stdout, result.stderr) == (3, "linked\n", "")


def test_names_root_in_llvm_ir_and_assembly(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    assert run_build(tmp_path, root, "--emit", "llvm-ir,asm").returncode == 0

    llvm_ir = (tmp_path / "app.ll").read_text()
    assert llvm_ir.startswith(f"; ModuleID = 'app'\nsource_filename = \"{root}\"\n")
    # Like clang, LLVM names only the source file's base name in the assembly.
    assert '\t.file\t"app.leech"\n' in (tmp_path / "app.s").read_text()


def test_escapes_source_filename(tmp_path):
    root = write(tmp_path / 'we"ird\\dir' / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, "--emit", "llvm-ir,obj")

    assert proc.returncode == 0, proc.stderr
    # LLVM reprints the parsed name with its own escapes.
    escaped = str(root).replace("\\", "\\\\").replace('"', "\\22")
    assert f'source_filename = "{escaped}"\n' in (tmp_path / "app.ll").read_text()


def test_rejects_abbreviated_long_options(tmp_path):
    root = write(tmp_path / "app.leech", _HELLO)

    proc = run_build(tmp_path, root, "--emi", "llvm-ir")

    assert proc.returncode == 2
    assert "unrecognized arguments: --emi llvm-ir" in proc.stderr


def test_program_reports_its_own_warnings(tmp_path):
    warning_path = tmp_path / "app.leech"
    warning_path.write_text("pub fn f() i32 { return 1; return 2; }\n")
    clean_path = tmp_path / "clean.leech"
    clean_path.write_text("pub fn f() i32 { return 1; }\n")
    warned = session.Session()
    clean = session.Session()

    program.Program(warning_path, entry=False).check(warned).llvm_ir()
    program.Program(clean_path, entry=False).check(clean).llvm_ir()

    assert warned.diags.level == errors.WARNING
    assert [type(d) for d in warned.diags.all()] == [errors.UnreachableCodeWarning]
    assert clean.diags.all() == ()
    assert clean.diags.level == errors.NOTE


def test_program_reports_raised_error_after_warnings(tmp_path):
    src_path = tmp_path / "app.leech"
    src_path.write_text(
        "enum E { A }\n"
        "pub fn f(e: E) i32 { return match (e) { E::A => 1i32, _ => 2i32, }; }\n"
        "pub fn g() i32 { return true; }\n"
    )
    compilation = session.Session()

    with pytest.raises(errors.InvalidRetTypError):
        program.Program(src_path, entry=False).check(compilation)

    assert compilation.diags.level == errors.ERROR
    assert [type(d) for d in compilation.diags.all()] == [
        errors.UnreachableMatchArmWarning,
        errors.InvalidRetTypError,
    ]


def test_program_fails_on_emitted_error(tmp_path, monkeypatch):
    src_path = tmp_path / "app.leech"
    src_path.write_text("pub fn f() i32 { return 0; }\n")
    harness.emit_error_while_checking(monkeypatch)
    compilation = session.Session()

    with pytest.raises(errors.CcNotFoundError):
        program.Program(src_path, entry=False).check(compilation)

    assert [type(d) for d in compilation.diags.all()] == [errors.CcNotFoundError]
