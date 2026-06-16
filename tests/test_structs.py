import pytest

from l0.l0errors import (
    DuplicateFieldInStructExprError,
    DuplicateFieldInStructDefnError,
    FieldAccessIntoInvalidTypError,
    IncompatibleStructFieldTypError,
    InvalidStructFieldError,
    MissingFieldInStructExprError,
    TypNotFoundError,
    TypeOfStructExprNotStructError,
)

from util import check_prog_output, compile_str


def test_struct_access(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;

    let a = [1, 2, 3, 4];

    struct T {
      a: i32,
      b: *u8,
      c: [i32; 4],
    }

    pub fn main() i32 {
        let mut t = T { a: 10, b: "abc", c: a};
        puts(t.b);
        t.a = 9;
        return t.a + t.c[3usize];
    }
    """
    check_prog_output(tmp_path, src, "abc\n", 13)


def test_duplicate_field_in_struct_defn(tmp_path):
    src = """
    struct T {
      a: i32,
      b: *u8,
      a: [i32; 4],
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(DuplicateFieldInStructDefnError):
        compile_str(tmp_path, src)


def test_unknown_typ_for_struct_field(tmp_path):
    src = """
    struct T {
      a: *xyz,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(TypNotFoundError):
        compile_str(tmp_path, src)


def test_struct_in_struct(tmp_path):
    src = """
    struct T {
      a: i32,
    }
    struct U {
      b: T,
    }
    pub fn main() i32 {
        let x = U{b: T{a: 100}};
        return x.b.a;
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_struct_with_ptr_to_same_struct(tmp_path):
    src = """
    struct T {
      a: i32,
      t: *T,
    }
    pub fn main() i32 {
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_typ_of_struct_expr_not_struct(tmp_path):
    src = """
    pub fn main() i32 {
        let a = i32 {a: 0};
        return 0;
    }
    """
    with pytest.raises(TypeOfStructExprNotStructError):
        compile_str(tmp_path, src)


def test_missing_field_in_struct_expr(tmp_path):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    pub fn main() i32 {
        let x = T {a: 0};
        return 0;
    }
    """
    with pytest.raises(MissingFieldInStructExprError):
        compile_str(tmp_path, src)


def test_invalid_field_in_struct_expr(tmp_path):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    pub fn main() i32 {
        let x = T {a: 0, b: 0, c: 0};
        return 0;
    }
    """
    with pytest.raises(InvalidStructFieldError):
        compile_str(tmp_path, src)


def test_duplicate_field_in_struct_expr(tmp_path):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    pub fn main() i32 {
        let x = T {a: 0, b: 0, a: 0};
        return 0;
    }
    """
    with pytest.raises(DuplicateFieldInStructExprError):
        compile_str(tmp_path, src)


def test_incompatible_struct_field_typ(tmp_path):
    src = """
    struct T {
        a: i32,
    }
    pub fn main() i32 {
        let x = T {a: "abc"};
        return 0;
    }
    """
    with pytest.raises(IncompatibleStructFieldTypError):
        compile_str(tmp_path, src)


def test_field_access_into_non_struct(tmp_path):
    src = """
    pub fn main() i32 {
        let x = [0, 1, 2, 3];

        return x.a;
    }
    """
    with pytest.raises(FieldAccessIntoInvalidTypError):
        compile_str(tmp_path, src)


def test_comptime_struct_access(tmp_path):
    src = """
    struct T {
      a: i32,
      b: u8,
      c: bool,
    }

    let mut t = T { a: 10, b: 100u8, c: false};
    let x = t.a;
    let y = t.b;
    let z = t.c;

    pub fn main() i32 {
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 10)


def test_typ_of_comptime_struct_expr_not_struct(tmp_path):
    src = """
    let a = i32 {a: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(TypeOfStructExprNotStructError):
        compile_str(tmp_path, src)


def test_missing_field_in_comptime_struct_expr(tmp_path):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    let x = T {a: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(MissingFieldInStructExprError):
        compile_str(tmp_path, src)


def test_invalid_field_in_comptime_struct_expr(tmp_path):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    let x = T {a: 0, b: 0, c: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(InvalidStructFieldError):
        compile_str(tmp_path, src)


def test_duplicate_field_in_comptime_struct_expr(tmp_path):
    src = """
    struct T {
        a: i32,
        b: i32,
    }
    let x = T {a: 0, b: 0, a: 0};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(DuplicateFieldInStructExprError):
        compile_str(tmp_path, src)


def test_incompatible_comptime_struct_field_typ(tmp_path):
    src = """
    struct T {
        a: i32,
    }
    let x = T {a: "abc"};
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IncompatibleStructFieldTypError):
        compile_str(tmp_path, src)


def test_comptime_field_access_into_non_struct(tmp_path):
    src = """
    let x = [0, 1, 2, 3];
    let y = x.a;
    pub fn main() i32 {

        return y;
    }
    """
    with pytest.raises(FieldAccessIntoInvalidTypError):
        compile_str(tmp_path, src)
