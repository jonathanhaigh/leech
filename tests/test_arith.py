import pytest
from l0.l0errors import (
    DivisionByZeroAtComptimeError,
    IncompatibleBinOpArgTypsError,
    IntOverflowAtComptimeError,
    InvalidBinOpArgTypError,
    InvalidUnaryOpArgTypError,
)
from util import check_prog_output, compile_str


def test_int_arith(tmp_path):
    src = """
    pub fn main() i32 {
        return 1 - 2 * 3 + 4 - 5 * 6 / 10;
    }
    """
    check_prog_output(tmp_path, src, "", 256 - 4)


def test_comptime_int_arith(tmp_path):
    src = """
    let x = 1 - 2 * 3 + 4 - 5 * 6 / 10;
    pub fn main() i32 {
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 256 - 4)


@pytest.mark.parametrize(
    "lhs,rhs",
    (
        ("0u8", "0i8"),
        ("0i10", "0i11"),
        ("0u10", "0u11"),
    ),
)
def test_incompatible_bin_op_args(lhs, rhs, tmp_path):
    src = f"""
    pub fn main() i32 {{
        let x = {lhs} + {rhs};
        return 0;
    }}
    """
    with pytest.raises(IncompatibleBinOpArgTypsError):
        compile_str(tmp_path, src)


def test_invalid_bin_op_arg(tmp_path):
    src = """
    pub fn main() i32 {
        let x = true + false;
        return 0;
    }
    """
    with pytest.raises(InvalidBinOpArgTypError):
        compile_str(tmp_path, src)


def test_division_by_non_comptime_value_is_not_a_compile_error(tmp_path):
    # A divisor that's only known at runtime (a function parameter, not a
    # comptime-evaluated constant) is never checked against zero at compile
    # time - that's ordinary runtime UB, same as C/LLVM. Only compile-time
    # (comptime) division actually evaluates the divisor, so this must
    # compile successfully.
    src = """
    fn f(x: i32) i32 {
        return 10 / x;
    }

    pub fn main() i32 {
        return 0;
    }
    """
    compile_str(tmp_path, src)


def test_comptime_signed_division_by_zero(tmp_path):
    src = """
    let x = 5 / 0;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(DivisionByZeroAtComptimeError):
        compile_str(tmp_path, src)


def test_comptime_unsigned_division_by_zero(tmp_path):
    src = """
    let x = 5usize / 0usize;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(DivisionByZeroAtComptimeError):
        compile_str(tmp_path, src)


def test_comptime_division_by_zero_in_called_fn(tmp_path):
    src = """
    fn f() i32 {
        return 5 / 0;
    }
    let x = f();
    pub fn main() i32 {
        return x;
    }
    """
    with pytest.raises(DivisionByZeroAtComptimeError):
        compile_str(tmp_path, src)


def test_comptime_add_at_typ_max_is_allowed(tmp_path):
    src = """
    let x = 254u8 + 1u8;
    pub fn main() i32 {
        if (x == 255u8) {
            return 1;
        };
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 1)


def test_comptime_add_overflow(tmp_path):
    src = """
    let x = 255u8 + 1u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntOverflowAtComptimeError):
        compile_str(tmp_path, src)


def test_comptime_unsigned_sub_underflow(tmp_path):
    src = """
    let x = 0u8 - 1u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntOverflowAtComptimeError):
        compile_str(tmp_path, src)


def test_comptime_mul_overflow(tmp_path):
    src = """
    let x = 200u8 * 2u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntOverflowAtComptimeError):
        compile_str(tmp_path, src)


def test_comptime_signed_sub_underflow(tmp_path):
    # (0i8 - 127i8) is -127, which fits i8. Subtracting 127i8 again gives
    # -254, which doesn't (i8's range is -128..127) - a signed underflow
    # that a naive bit-length-based overflow check (checking magnitude
    # only, ignoring the sign) would miss.
    src = """
    let x = 0i8 - 127i8 - 127i8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntOverflowAtComptimeError):
        compile_str(tmp_path, src)


def test_comptime_signed_div_overflow(tmp_path):
    # -128i8 / -1i8 = 128, which doesn't fit in i8 (max 127): the classic
    # INT_MIN / -1 overflow case.
    src = """
    let neg128 = 0i8 - 127i8 - 1i8;
    let neg1 = 0i8 - 1i8;
    let x = neg128 / neg1;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntOverflowAtComptimeError):
        compile_str(tmp_path, src)


def test_int_arith_unary_minus(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 5;
        return -x - 3;
    }
    """
    check_prog_output(tmp_path, src, "", 256 - 8)


def test_comptime_unary_minus(tmp_path):
    src = """
    let x = -5 - 3;
    pub fn main() i32 {
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 256 - 8)


def test_comptime_neg_overflow(tmp_path):
    # i8's min value is -128, which has no positive counterpart representable
    # in i8 (max is 127) - negating it must be a compile error.
    src = """
    let neg128 = 0i8 - 127i8 - 1i8;
    let x = -neg128;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntOverflowAtComptimeError):
        compile_str(tmp_path, src)


def test_invalid_unary_op_arg_unsigned(tmp_path):
    src = """
    pub fn main() i32 {
        let x = -0u32;
        return 0;
    }
    """
    with pytest.raises(InvalidUnaryOpArgTypError):
        compile_str(tmp_path, src)


def test_invalid_unary_op_arg_non_int(tmp_path):
    src = """
    pub fn main() i32 {
        let x = -true;
        return 0;
    }
    """
    with pytest.raises(InvalidUnaryOpArgTypError):
        compile_str(tmp_path, src)


def test_comptime_int_operation_overflow_distinct_from_lit_overflow(tmp_path):
    # Neither operand literal (100i8) overflows on its own - only the
    # result of adding them does. This must raise IntOverflowAtComptimeError,
    # not IntLitOverflowError.
    src = """
    let x = 100i8 + 100i8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(IntOverflowAtComptimeError):
        compile_str(tmp_path, src)
