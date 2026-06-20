import pytest

from l0.l0errors import ItemNotFoundError
from util import compile_str


def test_param_typ_not_defined(tmp_path):
    src = """
    pub fn f(p: not_a_typ) { }
    pub fn main() i32 { 0 }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)
