# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest
import util

from leech import errors


def test_int_arith(tmp_path):
    src = """
    pub fn main() i32 {
        return 1 - 2 * 3 + 4 - 5 * 6 / 10;
    }
    """
    util.check_prog_output(tmp_path, src, "", 256 - 4)


def test_comptime_int_arith(tmp_path):
    src = """
    let x = 1 - 2 * 3 + 4 - 5 * 6 / 10;
    pub fn main() i32 {
        return x;
    }
    """
    util.check_prog_output(tmp_path, src, "", 256 - 4)


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
    with pytest.raises(errors.IncompatibleBinOpArgTypsError):
        util.compile_str(tmp_path, src)


def test_invalid_bin_op_arg(tmp_path):
    src = """
    pub fn main() i32 {
        let x = true + false;
        return 0;
    }
    """
    with pytest.raises(errors.InvalidBinOpArgTypError):
        util.compile_str(tmp_path, src)


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
    util.compile_str(tmp_path, src)


def test_signed_division_in_unreachable_code_is_not_a_compile_error(tmp_path):
    # A signed division built into an already-dead block (see
    # CfgBuilder._build_div_signed) must stay inert rather than build its
    # own overflow-check control flow into dead code.
    src = """
    fn f(x: i32, y: i32) i32 {
        return 0;
        return x / y;
    }

    pub fn main() i32 {
        return 0;
    }
    """
    util.compile_str(tmp_path, src)


def test_comptime_signed_division_by_zero(tmp_path):
    # Compile-time evaluation shares the same runtime safety checks as
    # codegen (see CfgBuilder._panic_if): this hits the same `panic` call
    # a runtime division by zero would, reported as PanicAtComptimeError.
    src = """
    let x = 5 / 0;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


def test_comptime_unsigned_division(tmp_path):
    src = """
    let x = 7usize / 2usize;
    pub fn main() i32 {
        if (x == 3usize) {
            return 1;
        };
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_comptime_unsigned_division_by_zero(tmp_path):
    src = """
    let x = 5usize / 0usize;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


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
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


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
    util.check_prog_output(tmp_path, src, "", 1)


def test_comptime_add_overflow(tmp_path):
    src = """
    let x = 255u8 + 1u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


def test_comptime_unsigned_sub_underflow(tmp_path):
    src = """
    let x = 0u8 - 1u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


def test_comptime_mul_overflow(tmp_path):
    src = """
    let x = 200u8 * 2u8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


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
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


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
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


def test_comptime_signed_div_truncates_toward_zero(tmp_path):
    # LLVM's sdiv truncates toward zero, unlike Python's `//`, which floors
    # toward negative infinity: -7 / 2 and 7 / -2 must both be -3, not -4.
    src = """
    let neg7 = 0i8 - 7i8;
    let neg2 = 0i8 - 2i8;
    let neg3 = 0i8 - 3i8;
    let a = neg7 / 2i8;
    let b = 7i8 / neg2;
    pub fn main() i32 {
        return if (a == neg3 and b == neg3) { 1 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_int_arith_unary_minus(tmp_path):
    src = """
    pub fn main() i32 {
        let x = 5;
        return -x - 3;
    }
    """
    util.check_prog_output(tmp_path, src, "", 256 - 8)


def test_comptime_unary_minus(tmp_path):
    src = """
    let x = -5 - 3;
    pub fn main() i32 {
        return x;
    }
    """
    util.check_prog_output(tmp_path, src, "", 256 - 8)


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
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)


@pytest.mark.parametrize(
    "decl",
    (
        # Both the inferred and the explicitly-suffixed spelling of i8's
        # minimum value. Negation of a literal is folded into a single
        # constant, so the check is against -128 rather than against the
        # 128 that appears in the source and wouldn't fit i8 on its own.
        "let x: i8 = -128;",
        "let x = -128i8;",
    ),
)
def test_negated_int_lit_at_signed_typ_min(tmp_path, decl):
    src = f"""
    pub fn main() i32 {{
        {decl}
        return if (x == 0i8 - 127i8 - 1i8) {{ 7 }} else {{ 0 }};
    }}
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_comptime_negated_int_lit_at_signed_typ_min(tmp_path):
    src = """
    let x: i8 = -128;
    pub fn main() i32 {
        return if (x == 0i8 - 127i8 - 1i8) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_negated_int_lit_at_i32_min(tmp_path):
    src = """
    pub fn main() i32 {
        let x = -2147483648i32;
        return if (x == 0i32 - 2147483647 - 1) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_bare_negated_int_lit_in_peer_position(tmp_path):
    src = """
    pub fn main() i32 {
        let x = -1 + 10i8;
        return if (x == 9) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


def test_negated_int_lit_overflow(tmp_path):
    src = """
    pub fn main() i32 {
        let x: i8 = -200;
        return 0;
    }
    """
    with pytest.raises(errors.IntLitOverflowError) as exc_info:
        util.compile_str(tmp_path, src)

    msg = str(exc_info.value)
    assert "-200" in msg
    assert '"i8"' in msg


def test_negated_int_lit_infers_unsigned_is_rejected(tmp_path):
    src = """
    pub fn main() i32 {
        let x: u8 = -1;
        return 0;
    }
    """
    with pytest.raises(errors.InvalidUnaryOpArgTypError):
        util.compile_str(tmp_path, src)


def test_invalid_unary_op_arg_unsigned(tmp_path):
    src = """
    pub fn main() i32 {
        let x = -0u32;
        return 0;
    }
    """
    with pytest.raises(errors.InvalidUnaryOpArgTypError):
        util.compile_str(tmp_path, src)


def test_invalid_unary_op_arg_non_int(tmp_path):
    src = """
    pub fn main() i32 {
        let x = -true;
        return 0;
    }
    """
    with pytest.raises(errors.InvalidUnaryOpArgTypError):
        util.compile_str(tmp_path, src)


def test_comptime_int_operation_overflow_distinct_from_lit_overflow(tmp_path):
    # Neither operand literal (100i8) overflows on its own - only the
    # result of adding them does. This must raise PanicAtComptimeError (the
    # runtime overflow check, evaluated at compile time), not
    # IntLitOverflowError (which only ever rejects a literal itself).
    src = """
    let x = 100i8 + 100i8;
    pub fn main() i32 {
        return 0;
    }
    """
    with pytest.raises(errors.PanicAtComptimeError):
        util.compile_str(tmp_path, src)
