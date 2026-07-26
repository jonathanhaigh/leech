import pytest

from l0.l0errors import (
    IncompatibleAssignmentTypError,
    IncompatibleBinOpArgTypsError,
    IncompatibleLetTypError,
    IncompatibleStructFieldTypError,
    InvalidArgTypError,
    InvalidRetTypError,
)
from util import check_prog_output, compile_str


def test_mut_ptr_coerces_to_const_ptr_arg(tmp_path):
    src = """
    fn get(p: *i32) i32 {
        return p.*;
    }
    pub fn main() i32 {
        let mut x = 42;
        return get(&x);
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_return(tmp_path):
    src = """
    fn f(p: *mut i32) *i32 {
        return p;
    }
    pub fn main() i32 {
        let mut x = 42;
        return f(&x).*;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_tail_expr(tmp_path):
    src = """
    fn f(p: *mut i32) *i32 { p }
    pub fn main() i32 {
        let mut x = 42;
        return f(&x).*;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_assignment(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let mut p = &x;
        let mut y = 7;
        p = &y;
        return p.*;
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_mut_ptr_coerces_to_const_ptr_struct_field(tmp_path):
    src = """
    struct Holder { p: *i32 }
    pub fn main() i32 {
        let mut x = 42;
        let h = Holder { p: &x };
        return h.p.*;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_mut_ptr_coerces_to_const_ptr_array_element(tmp_path):
    # The element type comes from the parameter, so the elements coerce.
    src = """
    fn first(a: [*i32; 2]) i32 {
        return a[0usize].*;
    }
    pub fn main() i32 {
        let mut x = 42;
        let mut y = 7;
        return first([&x, &y]);
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_const_ptr_does_not_coerce_to_mut_ptr_arg(tmp_path):
    # The reverse is unsound: it would hand out write access that the
    # place never granted.
    src = """
    fn set(p: *mut i32) {
        p.* = 1;
    }
    pub fn main() i32 {
        let x = 42;
        set(&x);
        return 0;
    }
    """
    with pytest.raises(InvalidArgTypError):
        compile_str(tmp_path, src)


def test_const_ptr_does_not_coerce_to_mut_ptr_return(tmp_path):
    src = """
    fn f(p: *i32) *mut i32 {
        return p;
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(InvalidRetTypError):
        compile_str(tmp_path, src)


def test_const_ptr_does_not_coerce_to_mut_ptr_assignment(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 42;
        let mut y = 7;
        let mut p = &y;
        p = &x;
        return 0;
    }
    """
    with pytest.raises(IncompatibleAssignmentTypError):
        compile_str(tmp_path, src)


def test_ptr_coercion_does_not_change_pointee(tmp_path):
    # Only the mutability may differ - the pointee type still has to
    # match exactly.
    src = """
    struct Holder { p: *u8 }
    pub fn main() i32 {
        let mut x = 42i32;
        let h = Holder { p: &x };
        return 0;
    }
    """
    with pytest.raises(IncompatibleStructFieldTypError):
        compile_str(tmp_path, src)


def test_comptime_mut_ptr_coerces_to_const_ptr(tmp_path):
    src = """
    struct Holder { p: *i32 }
    fn f() i32 {
        let mut x = 42;
        let h = Holder { p: &x };
        return h.p.*;
    }
    let y = f();
    pub fn main() i32 {
        return y;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


@pytest.mark.parametrize(
    "src_typ,dst_typ",
    (
        ("i8", "i16"),
        ("i8", "i64"),
        ("i32", "i33"),
        ("u8", "u16"),
        ("u8", "u64"),
        # An unsigned type fits in a signed one only with a bit to spare
        # for the sign.
        ("u8", "i9"),
        ("u8", "i32"),
        ("u31", "i32"),
    ),
)
def test_widening_int_coercion_allowed(src_typ, dst_typ, tmp_path):
    src = f"""
    fn f(x: {dst_typ}) {dst_typ} {{
        return x;
    }}
    pub fn main() i32 {{
        let a = 42{src_typ};
        f(a);
        return 0;
    }}
    """
    check_prog_output(tmp_path, src, "", 0)


@pytest.mark.parametrize(
    "src_typ,dst_typ",
    (
        # Narrowing loses values.
        ("i16", "i8"),
        ("u16", "u8"),
        # A signed type's negatives never fit in an unsigned one, however
        # wide.
        ("i8", "u64"),
        # ...and an unsigned type needs a spare bit to fit in a signed
        # one of the same width.
        ("u8", "i8"),
        ("u32", "i32"),
    ),
)
def test_narrowing_int_coercion_rejected(src_typ, dst_typ, tmp_path):
    src = f"""
    fn f(x: {dst_typ}) {dst_typ} {{
        return x;
    }}
    pub fn main() i32 {{
        let a = 42{src_typ};
        f(a);
        return 0;
    }}
    """
    with pytest.raises(InvalidArgTypError):
        compile_str(tmp_path, src)


def test_widening_sign_extends_signed_source(tmp_path):
    # -5i8 must widen to -5, not to 251. Tested with a comparison rather
    # than by returning the value: the two candidates differ by exactly
    # 256, which an exit status can't tell apart.
    src = """
    fn as_i32(x: i32) i32 {
        return x;
    }
    pub fn main() i32 {
        let neg = 0i8 - 5i8;
        return if (as_i32(neg) < 0i32) {
            7
        } else {
            0
        };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_widening_zero_extends_unsigned_source(tmp_path):
    # 200u8 must widen to 200, not to -56.
    src = """
    fn as_i32(x: i32) i32 {
        return x;
    }
    pub fn main() i32 {
        let big = 200u8;
        return if (as_i32(big) > 0i32) {
            7
        } else {
            0
        };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_widening_int_coercion_return(tmp_path):
    src = """
    fn f() i64 {
        let a = 42i8;
        return a;
    }
    pub fn main() i32 {
        f();
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_widening_int_coercion_tail_expr(tmp_path):
    src = """
    fn f() i64 { 42i8 }
    pub fn main() i32 {
        f();
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_widening_int_coercion_assignment(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 0i32;
        let small = 42i8;
        x = small;
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_widening_int_coercion_struct_field(tmp_path):
    src = """
    struct T { a: i32 }
    pub fn main() i32 {
        let small = 42i8;
        let t = T { a: small };
        return t.a;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_widening_int_coercion_array_element(tmp_path):
    src = """
    fn first(a: [i32; 2]) i32 {
        return a[0usize];
    }
    pub fn main() i32 {
        let small = 42i8;
        let other = 7i16;
        return first([small, other]);
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_widening_int_coercion_let_initializer(tmp_path):
    # The `: i64` annotation makes the let statement a coercion point.
    # The suffix on -10i8 is what keeps this a coercion test: without it
    # the literal would simply be inferred as an i64 and nothing would be
    # widened. `+` never coerces its operands, so adding another i64 only
    # type-checks if x is actually i64.
    src = """
    pub fn main() i32 {
        let x: i64 = -10i8;
        let y = x + 20i64;
        return if (y == 10i64) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_narrowing_int_coercion_let_rejected(tmp_path):
    src = """
    pub fn main() i32 {
        let x: i8 = 1i16;
        return 0;
    }
    """
    with pytest.raises(IncompatibleLetTypError):
        compile_str(tmp_path, src)


def test_mut_ptr_coerces_to_const_ptr_let_initializer(tmp_path):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let p: *i32 = &x;
        return p.*;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_comptime_widening_int_coercion_let(tmp_path):
    # Module-level initializers are evaluated by the interpreter, so an
    # annotated `let` has to widen there too.
    src = """
    let x: i64 = 42i8;
    pub fn main() i32 {
        let y = x + 1i64;
        return if (y == 43i64) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


def test_comptime_widening_int_coercion(tmp_path):
    # Module-level initializers are evaluated by the interpreter, so it
    # has to understand the widening instruction too.
    src = """
    struct T { a: i64 }
    fn f() i64 {
        let small = 42i8;
        let t = T { a: small };
        return t.a;
    }
    let x = f();
    fn as_i32(v: i32) i32 { return v; }
    pub fn main() i32 {
        let small = 7i8;
        return as_i32(small) + 35i32;
    }
    """
    check_prog_output(tmp_path, src, "", 42)


def test_no_coercion_in_arithmetic(tmp_path):
    # Operands have no single target type, so they never coerce - even
    # when one would legally widen to the other.
    src = """
    pub fn main() i32 {
        let a = 1i8;
        let b = 1i16;
        let c = a + b;
        return 0;
    }
    """
    with pytest.raises(IncompatibleBinOpArgTypsError):
        compile_str(tmp_path, src)


def test_no_coercion_in_comparison(tmp_path):
    src = """
    pub fn main() i32 {
        let a = 1i8;
        let b = 1i16;
        let c = a < b;
        return 0;
    }
    """
    with pytest.raises(IncompatibleBinOpArgTypsError):
        compile_str(tmp_path, src)


def test_no_coercion_in_let_initializer(tmp_path):
    # A `let` has no declared type to coerce towards, so the initializer
    # keeps its own type rather than widening.
    src = """
    fn takes_i8(x: i8) i8 { return x; }
    pub fn main() i32 {
        let a = 42i8;
        let b = a;
        takes_i8(b);
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_bool_does_not_coerce_to_int(tmp_path):
    src = """
    fn f(x: i32) i32 { return x; }
    pub fn main() i32 {
        f(true);
        return 0;
    }
    """
    with pytest.raises(InvalidArgTypError):
        compile_str(tmp_path, src)
