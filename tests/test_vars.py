import pytest

from l0.l0errors import DuplicateItemDefnError, ItemNotFoundError
from util import check_prog_output, compile_str


def test_int_mod_var_ref_in_fn(tmp_path):
    src = """
    let a = 100;
    pub fn main() i32 {
        return a;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_str_mod_var_ref_in_fn(tmp_path):
    src = """
    let s = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(s);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)


def test_int_mod_var_ref_in_mod(tmp_path):
    src = """
    let a = 100;
    let b = a;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        return b;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_str_mod_var_ref_in_mod(tmp_path):
    src = """
    let b = a;
    let a = "abcd";

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)


def test_var_not_found_at_mod_scope(tmp_path):
    src = """
    let a = x;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_var_not_found_at_fn_scope(tmp_path):
    src = """
    pub fn main() i32 { x }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_shadowed_mod_var(tmp_path):
    src = """
    let x = 100;
    pub fn main() i32 {
        let x = 200;
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 200)


def test_unshadowed_mod_var(tmp_path):
    src = """
    let x = 100;
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_shadowed_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
            return x;
        };
    }
    """
    check_prog_output(tmp_path, src, "", 200)


def test_unshadowed_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        {
            let x = 200;
        };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_cannot_access_inner_scope(tmp_path):
    src = """
    pub fn main() i32 {
        {
            let x = 200;
        };
        return x;
    }
    """
    with pytest.raises(ItemNotFoundError):
        compile_str(tmp_path, src)


def test_duplicate_mod_var(tmp_path):
    src = """
    let x = 100;
    let x = 200;
    pub fn main() i32 { 0 }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


def test_duplicate_local_var(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 100;
        let x = 200;
        return 0;
    }
    """
    with pytest.raises(DuplicateItemDefnError):
        compile_str(tmp_path, src)


@pytest.mark.xfail
def test_mod_var_cycle(tmp_path):
    src = """
    let b = a;
    let a = b;

    extern fn puts(s: *u8) i32;

    pub fn main() i32 {
        puts(b);
        return 100;
    }
    """
    check_prog_output(tmp_path, src, "abcd\n", 100)
