# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
import signal
import subprocess

import pytest

from leech import diag, diag_kinds
from leech import src as leech_src
from tests import harness


def test_src_position_returns_first_occurrence():
    assert harness.src_position("x\n  x", "x") == (1, 1)


def test_src_position_reports_multiline_loc():
    assert harness.src_position("first\n  target", "target") == (2, 3)


def test_src_position_reports_missing_substring():
    with pytest.raises(AssertionError, match="substring 'target' not present in the source"):
        harness.src_position("first", "target")


def test_assert_span_at_accepts_matching_start(compiler: harness.CompilerHarness):
    src = "first\n  target"
    path = compiler.write_mod(harness.ModSrc("helper", src))
    file = leech_src.SrcFile(path)
    span = leech_src.SrcSpan(
        file=file,
        start=8,
        end=14,
        start_line=2,
        end_line=2,
        start_col=3,
        end_col=9,
    )

    assert harness.assert_span_at(span, src, "target") is span


def test_assert_span_at_reports_path_substring_and_locs(
    compiler: harness.CompilerHarness,
):
    src = "first\n  target"
    path = compiler.write_mod(harness.ModSrc("helper", src))
    file = leech_src.SrcFile(path)
    span = leech_src.SrcSpan(
        file=file,
        start=0,
        end=1,
        start_line=4,
        end_line=4,
        start_col=5,
        end_col=6,
    )

    with pytest.raises(AssertionError) as exc_info:
        harness.assert_span_at(span, src, "target")

    message = str(exc_info.value)
    assert "helper.leech" in message
    assert "target" in message
    assert "(2, 3)" in message
    assert "(4, 5)" in message


def test_assert_span_at_reports_missing_span():
    with pytest.raises(
        AssertionError, match="expected span at \\(2, 3\\) for 'target', got no span"
    ):
        harness.assert_span_at(None, "first\n  target", "target")


def test_assert_span_at_rejects_source_from_another_file(compiler: harness.CompilerHarness):
    src = "first\n  target"
    path = compiler.write_mod(harness.ModSrc("helper", src))
    file = leech_src.SrcFile(path)
    span = leech_src.SrcSpan(
        file=file,
        start=8,
        end=14,
        start_line=2,
        end_line=2,
        start_col=3,
        end_col=9,
    )

    with pytest.raises(AssertionError, match=r"span file .*helper.leech does not match"):
        harness.assert_span_at(span, "target", "target")


def test_mod_src_derives_path_from_name():
    mod = harness.ModSrc("pkg::helper", "let answer = 42i32;")

    assert mod.path == pathlib.Path("pkg/helper.leech")


@pytest.mark.parametrize("name", ["", "::helper", "helper::", "pkg::i32", "std::helper", "std"])
def test_mod_src_rejects_invalid_name(name):
    with pytest.raises(ValueError):
        harness.ModSrc(name, "")


def test_mod_src_allows_reserved_intermediate_name_segment():
    mod = harness.ModSrc("array::helper", "")

    assert mod.path == pathlib.Path("array/helper.leech")


def test_program_from_main_adds_supporting_mods():
    helper = harness.ModSrc("helper", "")

    program = harness.TestProgram.from_main("fn main() {}", helper)

    assert program.root.name == "main"
    assert program.root.src == "fn main() {}"
    assert program.root.path == pathlib.Path("main.leech")
    assert program.mods == (helper,)


def test_program_rejects_duplicate_mod_names():
    first = harness.ModSrc("helper", "")
    second = harness.ModSrc("helper", "x")

    with pytest.raises(ValueError, match="duplicate module names"):
        harness.TestProgram.from_main("", first, second)


def test_program_rejects_a_root_named_by_several_segments():
    with pytest.raises(ValueError, match="root module name has several segments"):
        harness.TestProgram(harness.ModSrc("pkg::app", ""))


def test_compiler_fixture_uses_test_workspace(
    compiler: harness.CompilerHarness,
    tmp_path: pathlib.Path,
):
    assert compiler.workspace == tmp_path


def test_harness_instances_have_independent_workspaces(tmp_path_factory):
    first_path = tmp_path_factory.mktemp("first")
    second_path = tmp_path_factory.mktemp("second")

    first = harness.CompilerHarness(first_path)
    second = harness.CompilerHarness(second_path)

    assert first.workspace != second.workspace


def test_harnesses_write_only_to_their_own_workspaces(tmp_path_factory):
    first = harness.CompilerHarness(tmp_path_factory.mktemp("first-run"))
    second = harness.CompilerHarness(tmp_path_factory.mktemp("second-run"))

    assert first.run("pub fn main() i32 { 1 }").returncode == 1
    assert second.run("pub fn main() i32 { 2 }").returncode == 2

    expected_artifacts = {
        pathlib.Path("main.leech"),
        pathlib.Path("main.ll"),
        pathlib.Path(".link/program.o"),
        pathlib.Path(".link/program"),
    }
    for compiler in (first, second):
        artifacts = {
            path.relative_to(compiler.workspace)
            for path in compiler.workspace.rglob("*")
            if path.is_file()
        }
        assert artifacts == expected_artifacts


def test_harness_coerces_src_string_to_main_program():
    program = harness.CompilerHarness._coerce_program("fn main() {}")

    assert program.root.name == "main"
    assert program.root.src == "fn main() {}"
    assert program.root.path == pathlib.Path("main.leech")
    assert program.mods == ()


def test_parse_records_explicit_mod_path(compiler: harness.CompilerHarness):
    mod = harness.ModSrc("pkg::helper", "fn f() {}")

    parsed = compiler.parse(mod)

    assert parsed.span.file.path == compiler.workspace / "pkg/helper.leech"


def test_parse_raises_a_reported_syntax_error(compiler: harness.CompilerHarness):
    with pytest.raises(diag.ReportedError) as exc_info:
        compiler.parse("fn broken(")
    assert exc_info.value.reported.diag.kind == diag_kinds.UNEXPECTED_TOKEN


def test_build_materializes_imports(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "import helper; pub fn main() i32 { helper::answer() }",
        harness.ModSrc("helper", "pub fn answer() i32 { 42 }"),
    )

    mod = compiler.build(program)

    assert mod.name == "main"


def test_build_propagates_user_error(compiler: harness.CompilerHarness):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.build("fn invalid() i32 { true }")
    assert exc_info.value.kinds == (diag_kinds.RETURN_TYPE_MISMATCH,)


def test_success_does_not_print(compiler: harness.CompilerHarness, capsys):
    compiler.build("pub fn main() i32 { 0 }")
    compiler.compile("pub fn main() i32 { 0 }")
    compiler.run("pub fn main() i32 { 0 }")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_materialization_rejects_symlink_escape(
    compiler: harness.CompilerHarness,
    tmp_path_factory,
):
    outside = tmp_path_factory.mktemp("outside")
    (compiler.workspace / "escape").symlink_to(outside, target_is_directory=True)
    program = harness.TestProgram.from_main(
        "pub fn main() i32 { 0 }",
        harness.ModSrc("escape::helper", ""),
    )

    with pytest.raises(ValueError, match="escapes the compiler workspace"):
        compiler.build(program)

    assert not (outside / "helper.leech").exists()
    assert not (compiler.workspace / "main.leech").exists()


def test_write_mod_returns_materialized_path(compiler: harness.CompilerHarness):
    mod = harness.ModSrc("helper", "λ")

    path = compiler.write_mod(mod)

    assert path == compiler.workspace / "helper.leech"
    assert path.read_text(encoding="utf-8") == mod.src


def test_compile_writes_the_programs_ir_beside_its_root(compiler: harness.CompilerHarness):
    compiled = compiler.compile("pub fn main() i32 { 0 }")

    assert compiled.llvm_path == compiler.workspace / "main.ll"
    assert compiled.llvm_path.read_text(encoding="utf-8") == compiled.llvm_ir
    assert 'define i32 @"main::main"' in compiled.llvm_ir
    assert 'define i32 @"main"()' not in compiled.llvm_ir
    assert repr(compiled) == f"CompiledProgram(llvm_path={compiled.llvm_path!r})"


def test_each_compile_has_its_own_diags(compiler: harness.CompilerHarness):
    warned = compiler.compile("pub fn main() i32 { return 0; return 1; }")
    clean = compiler.compile("pub fn main() i32 { return 0; }")

    assert [d.kind for d in warned.diags.all()] == [diag_kinds.UNREACHABLE_CODE]
    assert clean.diags.all() == ()


def test_compile_emits_to_the_given_diags(compiler: harness.CompilerHarness):
    diags = diag.Diags()

    compiled = compiler.compile("pub fn main() i32 { return 0; return 1; }", diags=diags)

    assert compiled.diags is diags
    assert [d.kind for d in diags.all()] == [diag_kinds.UNREACHABLE_CODE]


def test_compile_generates_every_imported_module(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "import pkg::a; pub fn main() i32 { a::answer() }",
        harness.ModSrc(
            "pkg::a",
            "import pkg::sub::helper; pub fn answer() i32 { helper::answer() }",
        ),
        harness.ModSrc("pkg::sub::helper", "pub fn answer() i32 { 42 }"),
    )

    compiled = compiler.compile(program)

    for symbol in ("main::main", "pkg::a::answer", "pkg::sub::helper::answer"):
        assert f'define i32 @"{symbol}"()' in compiled.llvm_ir
    assert [path.name for path in compiler.workspace.rglob("*.ll")] == ["main.ll"]


def test_compile_propagates_user_error(compiler: harness.CompilerHarness):
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile("pub fn main() i32 { true }")
    assert exc_info.value.kinds == (diag_kinds.RETURN_TYPE_MISMATCH,)


def test_run_returns_separate_streams_and_status(compiler: harness.CompilerHarness):
    result = compiler.run('pub fn main() i32 { panic("failed"); }')

    assert result.stdout == ""
    assert result.stderr.startswith("failed\n")
    assert result.returncode == -signal.SIGABRT


def test_check_uses_exact_expectations(compiler: harness.CompilerHarness):
    compiler.check("pub fn main() i32 { 7 }", exit_status=7)


def test_check_signal_accepts_stable_stderr_prefix(compiler: harness.CompilerHarness):
    compiler.check_signal(
        'pub fn main() i32 { panic("failed"); }',
        expected_signal=signal.SIGABRT,
        stderr_prefix="failed\n",
    )


def test_run_automatically_links_standard_library(compiler: harness.CompilerHarness):
    compiler.check(
        'import std::io; pub fn main() i32 { io::println("hello"); 0 }',
        stdout="hello\n",
    )


def test_run_accepts_root_with_any_name(compiler: harness.CompilerHarness):
    program = harness.TestProgram(harness.ModSrc("app", "pub fn main() i32 { 6 }"))

    compiler.check(program, exit_status=6)


def test_compile_with_entry_defines_c_main_for_root_only(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "import helper; pub fn main() i32 { helper::main() }",
        harness.ModSrc("helper", "pub fn main() i32 { 0 }"),
    )

    compiled = compiler.compile(program, entry=True)

    assert compiled.llvm_ir.count('define i32 @"main"()') == 1
    assert 'define i32 @"helper::main"()' in compiled.llvm_ir


def test_check_failure_reports_expected_and_actual(compiler: harness.CompilerHarness):
    with pytest.raises(AssertionError) as exc_info:
        compiler.check("pub fn main() i32 { 7 }", exit_status=8)

    message = str(exc_info.value)
    assert "unexpected exit status" in message
    assert "expected 8" in message
    assert "got 7" in message
    assert str(compiler.workspace) in message


def test_materialization_rejects_reserved_runtime_path(compiler: harness.CompilerHarness):
    (compiler.workspace / ".link").mkdir()
    (compiler.workspace / "runtime").symlink_to(
        compiler.workspace / ".link", target_is_directory=True
    )
    program = harness.TestProgram.from_main(
        "pub fn main() i32 { 0 }",
        harness.ModSrc("runtime::helper", ""),
    )

    with pytest.raises(ValueError, match="reserved harness directory"):
        compiler.compile(program)


def test_link_failure_reports_process_details(compiler: harness.CompilerHarness, monkeypatch):
    def fail(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="link stdout", stderr="link stderr")

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(AssertionError) as exc_info:
        compiler.run("pub fn main() i32 { 0 }")

    message = str(exc_info.value)
    assert "linking failed" in message
    assert "return code: 1" in message
    assert "link stdout" in message
    assert "link stderr" in message
    assert str(compiler.workspace) in message


def test_link_timeout_reports_process_details(compiler: harness.CompilerHarness, monkeypatch):
    def time_out(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command, 30, output="partial stdout", stderr="partial stderr"
        )

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(AssertionError) as exc_info:
        compiler.run("pub fn main() i32 { 0 }")

    message = str(exc_info.value)
    assert "timed out after 30 seconds" in message
    assert "/.link/program.o" in message
    assert "partial stdout" in message
    assert "partial stderr" in message
    assert str(compiler.workspace) in message


def test_run_timeout_reports_process_details(compiler: harness.CompilerHarness, monkeypatch):
    real_run = subprocess.run

    def time_out_running_program(command, **kwargs):
        if command[0].endswith("/.link/program"):
            raise subprocess.TimeoutExpired(
                command, 30, output="partial stdout", stderr="partial stderr"
            )
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", time_out_running_program)

    with pytest.raises(AssertionError) as exc_info:
        compiler.run("pub fn main() i32 { 0 }")

    message = str(exc_info.value)
    assert "timed out after 30 seconds" in message
    assert "partial stdout" in message
    assert "partial stderr" in message
    assert str(compiler.workspace) in message
