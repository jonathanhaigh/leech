import pytest

from leech.leecherrors import DuplicateItemDefnError, ItemNotFoundError
from util import check_prog_output, compile_str


def test_param_typ_not_defined(tmp_path):
    src = """
    pub fn f(p: not_a_typ) { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


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
    check_prog_output(tmp_path, src, "", 5)


def test_duplicate_param_name(tmp_path):
    src = """
    fn f(x: i32, x: i32) i32 {
        return x;
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)
