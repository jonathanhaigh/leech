# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors
from tests import util


def test_param_typ_not_defined(compiler):
    src = """
    pub fn f(p: not_a_typ) { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(errors.ItemNotFoundError):
        compiler.compile(src)


def test_param_shadows_mod_var(tmp_path):
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
    util.check_prog_output(tmp_path, src, "", 5)


def test_duplicate_param_name(compiler):
    src = """
    fn f(x: i32, x: i32) i32 {
        return x;
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.DuplicateItemDefnError):
        compiler.compile(src)
