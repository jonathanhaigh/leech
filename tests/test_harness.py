# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pathlib

import pytest

from leech import errors
from tests import harness


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
