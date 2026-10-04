# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import driver, errors
from leech import src as leech_src
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


def test_transitive_nested_import_resolves_relative_to_its_own_file(compiler):
    # a's own `import sub::helper;` must resolve relative to a's directory
    # (pkg), not the root - if it resolved relative to the root instead,
    # sub/helper.leech wouldn't exist.
    main_src = """
    import pkg::a;
    pub fn main() i32 {
        return a::viaa();
    }
    """
    a_src = """
    import sub::helper;
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
        harness.ModSrc(
            "sub::helper",
            helper_src,
            path="pkg/sub/helper.leech",
        ),
    )
    compiler.check(program, exit_status=11)


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
    # since llvm-link would otherwise see two clashing (or, worse,
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


def _compile_with_roots(main_path, *roots):
    return driver.compile_to_llvm_ir(leech_src.SrcFile(main_path), "main", search_roots=roots)


def test_import_path_makes_outside_module_importable(tmp_path):
    main_path = _write(tmp_path / "app" / "main.leech", "import lib; pub fn f() i32 { lib::g() }")
    _write(tmp_path / "libs" / "lib.leech", "pub fn g() i32 { 1 }")

    with pytest.raises(errors.ModDoesNotExistError):
        _compile_with_roots(main_path)
    assert 'call i32 @"lib::g"()' in _compile_with_roots(main_path, tmp_path / "libs")


def test_nested_import_resolves_inside_import_path(tmp_path):
    main_path = _write(
        tmp_path / "app" / "main.leech", "import pkg::lib; pub fn f() i32 { lib::g() }"
    )
    _write(tmp_path / "libs" / "pkg" / "lib.leech", "pub fn g() i32 { 1 }")

    assert 'call i32 @"pkg::lib::g"()' in _compile_with_roots(main_path, tmp_path / "libs")


def test_first_matching_import_path_wins(tmp_path):
    main_path = _write(tmp_path / "app" / "main.leech", "import lib; pub fn f() i32 { lib::one() }")
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write(first / "lib.leech", "pub fn one() i32 { 1 }")
    _write(second / "lib.leech", "pub fn two() i32 { 2 }")

    assert 'call i32 @"lib::one"()' in _compile_with_roots(main_path, first, second)
    with pytest.raises(errors.ItemNotFoundError):
        _compile_with_roots(main_path, second, first)


def test_importing_directory_precedes_import_paths(tmp_path):
    main_path = _write(
        tmp_path / "app" / "main.leech", "import lib; pub fn f() i32 { lib::near() }"
    )
    _write(tmp_path / "app" / "lib.leech", "pub fn near() i32 { 1 }")
    _write(tmp_path / "libs" / "lib.leech", "pub fn far() i32 { 2 }")

    assert 'call i32 @"lib::near"()' in _compile_with_roots(main_path, tmp_path / "libs")


def test_import_path_cannot_shadow_bundled_module(tmp_path):
    main_path = _write(
        tmp_path / "app" / "main.leech", 'import std::io; pub fn f() { io::println("hi"); }'
    )
    _write(tmp_path / "libs" / "std" / "io.leech", "pub fn other() {}")

    assert 'call void @"std::io::println"' in _compile_with_roots(main_path, tmp_path / "libs")


def test_directory_named_like_module_is_not_a_match(tmp_path):
    main_path = _write(tmp_path / "app" / "main.leech", "import lib; pub fn f() i32 { lib::g() }")
    (tmp_path / "first" / "lib.leech").mkdir(parents=True)
    _write(tmp_path / "second" / "lib.leech", "pub fn g() i32 { 1 }")

    llvm_ir = _compile_with_roots(main_path, tmp_path / "first", tmp_path / "second")

    assert 'call i32 @"lib::g"()' in llvm_ir
