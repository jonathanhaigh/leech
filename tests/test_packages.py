# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_sibling_import_unaffected(tmp_path):
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
    util.check_prog_output(tmp_path, main_src, "", 42, a=a_src)


def test_nested_import_resolves_subdirectory(tmp_path):
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
    util.check_prog_output(tmp_path, main_src, "", 7, **{"sub/helper": helper_src})


def test_transitive_nested_import_resolves_relative_to_its_own_file(tmp_path):
    # a's own `import sub::helper;` must resolve relative to a's directory
    # (tmp_path/pkg), not the root's (tmp_path) - if it resolved relative
    # to the root instead, tmp_path/sub/helper.leech wouldn't exist.
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
    util.check_prog_output(
        tmp_path,
        main_src,
        "",
        11,
        {"pkg/sub/helper": "sub.helper"},
        **{"pkg/a": a_src, "pkg/sub/helper": helper_src},
    )


def test_nested_import_does_not_exist(tmp_path):
    main_src = """
    import sub::nope;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.ModDoesNotExistError) as exc_info:
        util.compile_modules(tmp_path, main=main_src)
    assert "sub::nope" in str(exc_info.value)


def test_same_stem_modules_in_different_subdirectories(tmp_path):
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
    util.check_prog_output(
        tmp_path,
        main_src,
        "",
        7,
        a=a_src,
        b=b_src,
        **{"a_pkg/mem": a_pkg_mem_src, "b_pkg/mem": b_pkg_mem_src},
    )
