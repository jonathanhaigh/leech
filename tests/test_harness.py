# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib
import signal
import subprocess
from typing import cast

import pytest

from leech import errors
from leech import src as leech_src
from tests import harness


def test_src_position_returns_first_occurrence():
    assert harness.src_position("x\n  x", "x") == (1, 1)


def test_src_position_reports_multiline_location():
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


def test_assert_span_at_reports_path_substring_and_locations(
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


def test_mod_src_accepts_string_path():
    mod = harness.ModSrc("helper", "", path="nested/source.leech")

    assert isinstance(mod.path, pathlib.Path)
    assert mod.path == pathlib.Path("nested/source.leech")


@pytest.mark.parametrize("name", ["", "::helper", "helper::", "pkg::i32", "prelude"])
def test_mod_src_rejects_invalid_name(name):
    with pytest.raises(ValueError):
        harness.ModSrc(name, "")


def test_mod_src_allows_reserved_intermediate_name_segment():
    mod = harness.ModSrc("array::helper", "")

    assert mod.path == pathlib.Path("array/helper.leech")


@pytest.mark.parametrize(
    "path",
    ["/tmp/helper.leech", "../helper.leech", "pkg/../helper.leech", "helper.txt"],
)
def test_mod_src_rejects_invalid_path(path):
    with pytest.raises(ValueError):
        harness.ModSrc("helper", "", path=path)


def test_mod_src_rejects_empty_path():
    with pytest.raises(ValueError, match="path must not be empty"):
        harness.ModSrc("helper", "", path="")


def test_program_from_main_adds_supporting_mods():
    helper = harness.ModSrc("helper", "")

    program = harness.TestProgram.from_main("fn main() {}", helper)

    assert program.root.name == "main"
    assert program.root.src == "fn main() {}"
    assert program.root.path == pathlib.Path("main.leech")
    assert program.mods == (helper,)


def test_program_rejects_duplicate_mod_names():
    first = harness.ModSrc("helper", "", path="first.leech")
    second = harness.ModSrc("helper", "", path="second.leech")

    with pytest.raises(ValueError, match="duplicate module names"):
        harness.TestProgram.from_main("", first, second)


def test_program_rejects_duplicate_mod_paths():
    first = harness.ModSrc("first", "", path="shared.leech")
    second = harness.ModSrc("second", "", path="shared.leech")

    with pytest.raises(ValueError, match="duplicate module paths"):
        harness.TestProgram.from_main("", first, second)


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


def test_harness_coerces_src_string_to_main_program():
    program = harness.CompilerHarness._coerce_program("fn main() {}")

    assert program.root.name == "main"
    assert program.root.src == "fn main() {}"
    assert program.root.path == pathlib.Path("main.leech")
    assert program.mods == ()


def test_parse_records_explicit_mod_path(compiler: harness.CompilerHarness):
    mod = harness.ModSrc("pkg::helper", "fn f() {}", path="pkg/helper.leech")

    parsed = compiler.parse(mod)

    assert parsed.span.file.path == compiler.workspace / "pkg/helper.leech"


def test_parse_propagates_user_error(compiler: harness.CompilerHarness):
    with pytest.raises(errors.UnexpectedTokenError):
        compiler.parse("fn broken(")


def test_build_materializes_imports(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "import helper; pub fn main() i32 { helper::answer() }",
        harness.ModSrc("helper", "pub fn answer() i32 { 42 }"),
    )

    mod = compiler.build(program)

    assert mod.name == "main"


def test_build_supports_exceptional_mod_path(compiler: harness.CompilerHarness):
    program = harness.TestProgram(
        harness.ModSrc(
            "pkg::a",
            "import sub::helper; pub fn answer() i32 { helper::answer() }",
            path="pkg/a.leech",
        ),
        (
            harness.ModSrc(
                "sub::helper",
                "pub fn answer() i32 { 42 }",
                path="pkg/sub/helper.leech",
            ),
        ),
    )

    mod = compiler.build(program)

    assert mod.name == "pkg::a"


def test_build_propagates_user_error(compiler: harness.CompilerHarness):
    with pytest.raises(errors.InvalidRetTypError):
        compiler.build("fn invalid() i32 { true }")


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
        harness.ModSrc("helper", "", path="escape/helper.leech"),
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


def test_compile_returns_structured_main_artifact(compiler: harness.CompilerHarness):
    compiled = compiler.compile("pub fn main() i32 { 0 }")

    main = compiled.mods["main"]
    assert main.mod.name == "main"
    assert main.src_path == compiler.workspace / "main.leech"
    assert main.llvm_path == compiler.workspace / "main.ll"
    assert main.llvm_path.read_text(encoding="utf-8") == main.llvm_ir
    assert 'define i32 @"main"' in main.llvm_ir
    assert "llvm_ir" not in repr(main)
    assert repr(compiled) == "CompiledProgram(mods=('main',))"


def test_compile_returns_artifacts_in_declaration_order(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "import a; pub fn main() i32 { a::answer() }",
        harness.ModSrc("a", "pub fn answer() i32 { 42 }"),
    )

    compiled = compiler.compile(program)

    assert tuple(compiled.mods) == ("main", "a")
    assert compiled.mods["a"].llvm_path == compiler.workspace / "a.ll"


def test_compile_supports_transitive_relative_import(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "import pkg::a; pub fn main() i32 { a::answer() }",
        harness.ModSrc(
            "pkg::a",
            "import sub::helper; pub fn answer() i32 { helper::answer() }",
        ),
        harness.ModSrc(
            "sub::helper",
            "pub fn answer() i32 { 42 }",
            path="pkg/sub/helper.leech",
        ),
    )

    compiled = compiler.compile(program)

    assert tuple(compiled.mods) == ("main", "pkg::a", "sub::helper")
    assert 'define i32 @"sub::helper::answer"' in compiled.mods["sub::helper"].llvm_ir


def test_compile_keeps_same_stem_mods_distinct(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "pub fn main() i32 { 0 }",
        harness.ModSrc("a_pkg::helper", "pub fn a() i32 { 1 }"),
        harness.ModSrc("b_pkg::helper", "pub fn b() i32 { 2 }"),
    )

    compiled = compiler.compile(program)

    assert compiled.mods["a_pkg::helper"].llvm_path == compiler.workspace / "a_pkg/helper.ll"
    assert compiled.mods["b_pkg::helper"].llvm_path == compiler.workspace / "b_pkg/helper.ll"


def test_compile_propagates_user_error(compiler: harness.CompilerHarness):
    with pytest.raises(errors.InvalidRetTypError):
        compiler.compile("pub fn main() i32 { true }")


def test_compiled_mod_mapping_is_read_only(compiler: harness.CompilerHarness):
    compiled = compiler.compile("pub fn main() i32 { 0 }")
    mutable_view = cast(dict[str, harness.CompiledMod], compiled.mods)

    with pytest.raises(TypeError):
        mutable_view["other"] = compiled.mods["main"]


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


def test_program_std_mod_shadows_bundled_mod(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        'import std::io; pub fn main() i32 { io::print("unused"); 9 }',
        harness.ModSrc("std::io", "pub fn print(msg: *u8) {}"),
    )

    compiler.check(program, stdout="", exit_status=9)


def test_run_requires_main_root(compiler: harness.CompilerHarness):
    program = harness.TestProgram(harness.ModSrc("app", "pub fn main() i32 { 0 }"))

    with pytest.raises(ValueError, match="root module must be named 'main'"):
        compiler.run(program)


def test_check_failure_reports_expected_and_actual(compiler: harness.CompilerHarness):
    with pytest.raises(AssertionError) as exc_info:
        compiler.check("pub fn main() i32 { 7 }", exit_status=8)

    message = str(exc_info.value)
    assert "unexpected exit status" in message
    assert "expected 8" in message
    assert "got 7" in message
    assert str(compiler.workspace) in message


def test_materialization_rejects_reserved_runtime_path(compiler: harness.CompilerHarness):
    program = harness.TestProgram.from_main(
        "pub fn main() i32 { 0 }",
        harness.ModSrc("helper", "", path=".bundled/helper.leech"),
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
    assert "llvm-link failed" in message
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
    assert "partial stdout" in message
    assert "partial stderr" in message
    assert str(compiler.workspace) in message
