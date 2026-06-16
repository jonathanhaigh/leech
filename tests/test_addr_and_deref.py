import pytest

from l0.l0errors import DerefInvalidTypError

from util import check_prog_output, compile_str


def test_addr_and_deref(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 10;
        let y = &x;
        return y.*;
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_addr_and_deref_field(tmp_path):
    src = """
    struct T {
        a: i32,
    }

    struct U {
        b: *i32,
    }

    pub fn main() i32 {
        let x = T {a: 11};
        let y = U {b: &x.a};
        return y.b.*;
    }
    """
    check_prog_output(tmp_path, src, "", 11)


def test_addr_and_deref_array_elt(tmp_path):
    src = """
    pub fn main() i32 {
        let x = [101, 102, 103];
        let y = [&x[2usize], &x[1usize], &x[0usize]];
        return y[1usize].*;
    }
    """
    check_prog_output(tmp_path, src, "", 102)


def test_addr_of_tmp(tmp_path):
    src = """
    fn deref(ptr: *i32) i32 {
        return ptr.*;
    }

    pub fn main() i32 {
        return deref(&100);
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_deref_non_ptr(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 10;
        return x.*;
    }
    """
    with pytest.raises(DerefInvalidTypError):
        compile_str(tmp_path, src)
