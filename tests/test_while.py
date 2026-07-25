import pytest

from l0.l0errors import WhileTypNotVoidError
from util import check_prog_output, compile_str


def test_while(tmp_path):
    src = """
    pub fn main() i32 {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        };
        return i;
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_comptime_while(tmp_path):
    src = """
    let x = {
        let mut i = 0;
        while (i < 10) {
            i = i + 1;
        };
        i
    };
    pub fn main() i32 {
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_if_in_while(tmp_path):
    src = """
    pub fn main() i32 {
        let mut i = 1;
        while (true) {
            i = 2 * i;
            if (i >= 120) {
                return i;
            }
        };
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 128)


def test_while_body_not_void(tmp_path):
    src = """
    pub fn main() i32 {
        while (true) {
            1
        };
        return 0;
    }
    """
    with pytest.raises(WhileTypNotVoidError):
        compile_str(tmp_path, src)
