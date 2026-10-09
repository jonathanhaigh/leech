# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds


def test_param_typ_not_defined(compiler):
    src = """
    pub fn f(p: not_a_typ) { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.UNKNOWN_NAME,)


def test_param_shadows_mod_var(compiler):
    # Inside f, x refers to the parameter, not the module-level variable
    # of the same name.
    src = """
    let x = 100;
    fn f(x: i32) i32 {
        return x;
    }
    pub fn main() i32 {
        return f(5);
    }
    """
    compiler.check(src, exit_status=5)


def test_duplicate_param_name(compiler):
    src = """
    fn f(x: i32, x: i32) i32 {
        return x;
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.DUPLICATE_DEFINITION,)
