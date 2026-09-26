# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import errors


def test_mut_ptr_coerces_to_const_ptr_arg(compiler):
    src = """
    fn get(p: *i32) i32 {
        return p.*;
    }
    pub fn main() i32 {
        let mut x = 42;
        return get(&x);
    }
    """
    compiler.check(src, exit_status=42)


def test_mut_ptr_coerces_to_const_ptr_return(compiler):
    src = """
    fn f(p: *mut i32) *i32 {
        return p;
    }
    pub fn main() i32 {
        let mut x = 42;
        return f(&x).*;
    }
    """
    compiler.check(src, exit_status=42)


def test_mut_ptr_coerces_to_const_ptr_tail_expr(compiler):
    src = """
    fn f(p: *mut i32) *i32 { p }
    pub fn main() i32 {
        let mut x = 42;
        return f(&x).*;
    }
    """
    compiler.check(src, exit_status=42)


def test_mut_ptr_coerces_to_const_ptr_assignment(compiler):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let mut p = &x;
        let mut y = 7;
        p = &y;
        return p.*;
    }
    """
    compiler.check(src, exit_status=7)


def test_mut_ptr_coerces_to_const_ptr_struct_field(compiler):
    src = """
    struct Holder { p: *i32 }
    pub fn main() i32 {
        let mut x = 42;
        let h = Holder { p: &x };
        return h.p.*;
    }
    """
    compiler.check(src, exit_status=42)


def test_mut_ptr_coerces_to_const_ptr_array_element(compiler):
    # The element type comes from the parameter, so the elements coerce.
    src = """
    fn first(a: array[*i32, 2]) i32 {
        return a.[0usize].*;
    }
    pub fn main() i32 {
        let mut x = 42;
        let mut y = 7;
        return first(array[*i32, 2]{&x, &y});
    }
    """
    compiler.check(src, exit_status=42)


def test_const_ptr_does_not_coerce_to_mut_ptr_arg(compiler):
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
    with pytest.raises(errors.InvalidArgTypError):
        compiler.compile(src)


def test_const_ptr_does_not_coerce_to_mut_ptr_return(compiler):
    src = """
    fn f(p: *i32) *mut i32 {
        return p;
    }
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.InvalidRetTypError):
        compiler.compile(src)


def test_const_ptr_does_not_coerce_to_mut_ptr_assignment(compiler):
    src = """
    pub fn main() i32 {
        let x = 42;
        let mut y = 7;
        let mut p = &y;
        p = &x;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleAssignmentTypError):
        compiler.compile(src)


def test_ptr_coercion_does_not_change_pointee(compiler):
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
    with pytest.raises(errors.IncompatibleStructFieldTypError):
        compiler.compile(src)


def test_comptime_mut_ptr_coerces_to_const_ptr(compiler):
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
    compiler.check(src, exit_status=42)


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
def test_widening_int_coercion_allowed(src_typ, dst_typ, compiler):
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
    compiler.check(src)


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
def test_narrowing_int_coercion_rejected(compiler, src_typ, dst_typ):
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
    with pytest.raises(errors.InvalidArgTypError):
        compiler.compile(src)


def test_widening_sign_extends_signed_source(compiler):
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
    compiler.check(src, exit_status=7)


def test_widening_zero_extends_unsigned_source(compiler):
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
    compiler.check(src, exit_status=7)


def test_widening_int_coercion_return(compiler):
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
    compiler.check(src)


def test_widening_int_coercion_tail_expr(compiler):
    src = """
    fn f() i64 { 42i8 }
    pub fn main() i32 {
        f();
        return 0;
    }
    """
    compiler.check(src)


def test_widening_int_coercion_assignment(compiler):
    src = """
    pub fn main() i32 {
        let mut x = 0i32;
        let small = 42i8;
        x = small;
        return x;
    }
    """
    compiler.check(src, exit_status=42)


def test_widening_int_coercion_struct_field(compiler):
    src = """
    struct T { a: i32 }
    pub fn main() i32 {
        let small = 42i8;
        let t = T { a: small };
        return t.a;
    }
    """
    compiler.check(src, exit_status=42)


def test_widening_int_coercion_array_element(compiler):
    src = """
    fn first(a: array[i32, 2]) i32 {
        return a.[0usize];
    }
    pub fn main() i32 {
        let small = 42i8;
        let other = 7i16;
        return first(array[i32, 2]{small, other});
    }
    """
    compiler.check(src, exit_status=42)


def test_widening_int_coercion_let_initializer(compiler):
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
    compiler.check(src, exit_status=7)


def test_let_widening_int_initializer_stores_into_declared_type(compiler):
    src = """
    pub fn main() i32 {
        let x: i64 = 1i32;
        return if (x == 1i64) { 5 } else { 0 };
    }
    """
    compiler.check(src, exit_status=5)


def test_narrowing_int_coercion_let_rejected(compiler):
    src = """
    pub fn main() i32 {
        let x: i8 = 1i16;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleLetTypError):
        compiler.compile(src)


def test_mut_ptr_coerces_to_const_ptr_let_initializer(compiler):
    src = """
    pub fn main() i32 {
        let mut x = 42;
        let p: *i32 = &x;
        return p.*;
    }
    """
    compiler.check(src, exit_status=42)


def test_let_mut_ptr_initializer_stores_into_declared_const_ptr(compiler):
    src = """
    pub fn main() i32 {
        let mut a = 5i32;
        let p: *i32 = &a;
        return p.*;
    }
    """
    compiler.check(src, exit_status=5)


def test_comptime_widening_int_coercion_let(compiler):
    # Module-level initializers are evaluated by the interpreter, so an
    # annotated `let` has to widen there too.
    src = """
    let x: i64 = 42i8;
    pub fn main() i32 {
        let y = x + 1i64;
        return if (y == 43i64) { 7 } else { 0 };
    }
    """
    compiler.check(src, exit_status=7)


def test_comptime_widening_int_coercion(compiler):
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
    compiler.check(src, exit_status=42)


def test_no_coercion_in_arithmetic(compiler):
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
    with pytest.raises(errors.IncompatibleBinOpArgTypsError):
        compiler.compile(src)


def test_no_coercion_in_comparison(compiler):
    src = """
    pub fn main() i32 {
        let a = 1i8;
        let b = 1i16;
        let c = a < b;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleBinOpArgTypsError):
        compiler.compile(src)


def test_no_coercion_in_let_initializer(compiler):
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
    compiler.check(src)


def test_bool_does_not_coerce_to_int(compiler):
    src = """
    fn f(x: i32) i32 { return x; }
    pub fn main() i32 {
        f(true);
        return 0;
    }
    """
    with pytest.raises(errors.InvalidArgTypError):
        compiler.compile(src)
