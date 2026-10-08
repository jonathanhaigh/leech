# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors, program, session
from tests import harness


def test_sibling_import_unaffected(compiler):
    # Regression: a plain single-segment import still resolves next to the
    # importing file, exactly as before ::-paths existed.
    main_src = """
    import a;
    pub fn main() i32 {
        return a::f();
    }
    """
    a_src = """
    pub fn f() i32 {
        return 42;
    }
    """
    program = harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src))
    compiler.check(program, exit_status=42)


def test_nested_import_resolves_subdirectory(compiler):
    # `import sub::helper;` binds only the last segment, `helper` - `sub`
    # is a pure filesystem path component, never bound anywhere.
    main_src = """
    import sub::helper;
    pub fn main() i32 {
        return helper::f();
    }
    """
    helper_src = """
    pub fn f() i32 {
        return 7;
    }
    """
    program = harness.TestProgram.from_main(
        main_src,
        harness.ModSrc("sub::helper", helper_src),
    )
    compiler.check(program, exit_status=7)


def test_nested_import_allows_reserved_directory_name(compiler):
    main_src = """
    import array::helper;
    pub fn main() i32 {
        return helper::f();
    }
    """
    helper_src = """
    pub fn f() i32 {
        return 7;
    }
    """
    program = harness.TestProgram.from_main(
        main_src,
        harness.ModSrc("array::helper", helper_src),
    )
    compiler.check(program, exit_status=7)


def test_nested_import_is_absolute_from_the_root_package(compiler):
    # pkg/a.leech names its neighbour pkg/sub/helper.leech by its full path from the root
    # package, the same as any other module would.
    main_src = """
    import pkg::a;
    pub fn main() i32 {
        return a::viaa();
    }
    """
    a_src = """
    import pkg::sub::helper;
    pub fn viaa() i32 {
        return helper::f() + 1;
    }
    """
    helper_src = """
    pub fn f() i32 {
        return 10;
    }
    """
    program = harness.TestProgram.from_main(
        main_src,
        harness.ModSrc("pkg::a", a_src),
        harness.ModSrc("pkg::sub::helper", helper_src),
    )
    compiler.check(program, exit_status=11)


def test_import_is_not_relative_to_the_importing_file(compiler):
    a_src = "import sub::helper;\npub fn viaa() i32 { return helper::f(); }"
    program = harness.TestProgram.from_main(
        "import pkg::a;\npub fn main() i32 { return a::viaa(); }",
        harness.ModSrc("pkg::a", a_src),
        harness.ModSrc("pkg::sub::helper", "pub fn f() i32 { return 10; }"),
    )

    with pytest.raises(errors.ModDoesNotExistError) as exc_info:
        compiler.build(program)

    harness.assert_span_at(exc_info.value.message.span, a_src, "sub::helper")


def test_nested_import_does_not_exist(compiler):
    main_src = """
    import sub::nope;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.ModDoesNotExistError) as exc_info:
        compiler.compile(main_src)
    assert "sub::nope" in str(exc_info.value)


@pytest.mark.parametrize(
    ("import_path", "offending_seg"),
    [("a[i32]", "a"), ("sub::a[i32]", "a"), ("sub[i32]::a", "sub")],
)
def test_import_path_rejects_comptime_args_before_missing_module_lookup(
    compiler, import_path, offending_seg
):
    main_src = f"""
    import {import_path};
    pub fn main() i32 {{ return 0; }}
    """

    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        compiler.compile(main_src)

    assert f'"{offending_seg}"' in str(exc_info.value)
    span = exc_info.value.message.span
    harness.assert_span_at(span, main_src, f"{offending_seg}[i32]")


def test_module_path_seg_rejects_comptime_args(compiler):
    main_src = """
    import a;
    pub fn main() i32 { return a[i32]::f(); }
    """
    a_src = "pub fn f() i32 { 0 }"

    with pytest.raises(errors.ComptimeArgsOnNonGenericItemError) as exc_info:
        compiler.compile(harness.TestProgram.from_main(main_src, harness.ModSrc("a", a_src)))
    assert '"a"' in str(exc_info.value)
    span = exc_info.value.message.span
    harness.assert_span_at(span, main_src, "a[i32]::f")


def test_same_stem_modules_in_different_subdirectories(compiler):
    # Two files both named mem.leech, in different packages, each
    # reached only through one intermediate wrapper module (so neither
    # bare name "mem" is ever bound twice in the same scope - only the
    # last import-path segment is bound at all). Both must be importable
    # and linkable in one program - this only works if the dotted
    # qualified name (not the bare file stem) drives the LLVM symbol,
    # since the linker would otherwise see two clashing (or, worse,
    # mismatched-across-compilation-units) `mem.f` symbols.
    main_src = """
    import a;
    import b;
    pub fn main() i32 {
        return a::f() + b::f();
    }
    """
    a_src = """
    import a_pkg::mem;
    pub fn f() i32 {
        return mem::f();
    }
    """
    b_src = """
    import b_pkg::mem;
    pub fn f() i32 {
        return mem::f();
    }
    """
    a_pkg_mem_src = """
    pub fn f() i32 {
        return 3;
    }
    """
    b_pkg_mem_src = """
    pub fn f() i32 {
        return 4;
    }
    """
    program = harness.TestProgram.from_main(
        main_src,
        harness.ModSrc("a", a_src),
        harness.ModSrc("b", b_src),
        harness.ModSrc("a_pkg::mem", a_pkg_mem_src),
        harness.ModSrc("b_pkg::mem", b_pkg_mem_src),
    )
    compiler.check(program, exit_status=7)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _compile(main_path):
    return program.Program(main_path, entry=False).check(session.Session()).llvm_ir()


def test_directory_named_like_module_is_not_a_module(tmp_path):
    main_path = _write(tmp_path / "app" / "main.leech", "import lib; pub fn f() i32 { lib::g() }")
    (tmp_path / "app" / "lib.leech").mkdir()

    with pytest.raises(errors.ModDoesNotExistError):
        _compile(main_path)


def test_non_root_module_compiles_against_the_root_package(compiler):
    # Every module is named after its location in the root's package, so the modules agree
    # on the names of the modules they share.
    program = harness.TestProgram.from_main(
        "import x::a;\nimport x::b;\npub fn main() i32 { return a::g() + b::h(); }",
        harness.ModSrc("x::a", "pub fn g() i32 { return 2; }"),
        harness.ModSrc("x::b", "import x::a;\npub fn h() i32 { return a::g() * 10; }"),
    )

    compiler.check(program, exit_status=22)


def test_reimport_shares_one_module(compiler):
    program = harness.TestProgram.from_main(
        "import a;\nimport b;\npub fn main() i32 { return a::f() + b::g(); }",
        harness.ModSrc("a", "import b; pub fn f() i32 { b::g() }"),
        harness.ModSrc("b", "pub fn g() i32 { 2 }"),
    )

    compiler.check(program, exit_status=4)


def test_import_linking_outside_every_package_is_reported(tmp_path):
    outside = _write(tmp_path / "outside" / "real.leech", "pub fn g() i32 { 3 }")
    main_src = "import alias;\npub fn f() i32 { alias::g() }"
    main_path = _write(tmp_path / "app" / "main.leech", main_src)
    (tmp_path / "app" / "alias.leech").symlink_to(outside)

    with pytest.raises(errors.ModOutsidePackagesError) as exc_info:
        _compile(main_path)

    harness.assert_span_at(exc_info.value.message.span, main_src, "alias;")
