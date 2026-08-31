# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import signal

import pytest
import util

from leech import errors

# --- if/else arms ---


@pytest.mark.parametrize(
    "then_arm,els_arm",
    (
        ("1u8", "2"),
        ("1", "2u8"),
    ),
)
def test_if_arm_typ_independent_of_order(then_arm, els_arm, tmp_path):
    src = f"""
    fn takes_u8(x: u8) u8 {{ return x; }}
    pub fn main() i32 {{
        let c = true;
        let x = if (c) {{ {then_arm} }} else {{ {els_arm} }};
        return takes_u8(x);
    }}
    """
    util.check_prog_output(tmp_path, src, "", 1)


def test_if_expected_typ_still_wins(tmp_path):
    src = """
    pub fn main() i32 {
        let c = false;
        let x: u8 = if (c) { 1 } else { 2 };
        return x;
    }
    """
    util.check_prog_output(tmp_path, src, "", 2)


def test_if_two_decided_arms_must_agree(tmp_path):
    src = """
    pub fn main() i32 {
        let c = true;
        let x = if (c) { 1u8 } else { 2u16 };
        return 0;
    }
    """
    with pytest.raises(errors.IfElsTypMismatchError):
        util.compile_str(tmp_path, src)


def test_if_diverging_arm_does_not_decide_typ(tmp_path):
    # The diverging arm says nothing about the type, so the bare literal
    # in the other arm is free to be a u8.
    src = """
    fn takes_u8(x: u8) u8 { return x; }
    pub fn main() i32 {
        let c = false;
        let x = if (c) { return 9; } else { 2u8 };
        return takes_u8(x);
    }
    """
    util.check_prog_output(tmp_path, src, "", 2)


# --- Binary operators ---


@pytest.mark.parametrize("expr", ("1u8 + 10", "1 + 10u8"))
def test_binop_operand_typ_independent_of_order(expr, tmp_path):
    src = f"""
    fn takes_u8(x: u8) u8 {{ return x; }}
    pub fn main() i32 {{
        let x = {expr};
        return takes_u8(x);
    }}
    """
    util.check_prog_output(tmp_path, src, "", 11)


def test_binop_takes_typ_from_context(tmp_path):
    # Neither operand decides its own type, so both take the declared
    # type of the variable the result goes into.
    src = """
    pub fn main() i32 {
        let x: u8 = 200 + 55;
        return if (x == 255u8) { 7 } else { 0 };
    }
    """
    util.check_prog_output(tmp_path, src, "", 7)


@pytest.mark.parametrize("expr", ("1u8 < 10", "1 < 10u8"))
def test_comparison_operand_typ_independent_of_order(expr, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if ({expr}) {{ 7 }} else {{ 0 }};
    }}
    """
    util.check_prog_output(tmp_path, src, "", 7)


@pytest.mark.parametrize("expr", ("1u8 + 2u16", "1u16 + 2u8"))
def test_binop_two_decided_operands_must_agree(expr, tmp_path):
    src = f"""
    pub fn main() i32 {{
        let x = {expr};
        return 0;
    }}
    """
    with pytest.raises(errors.IncompatibleBinOpArgTypsError):
        util.compile_str(tmp_path, src)


def test_binop_flexible_operand_does_not_adopt_non_int_typ(tmp_path):
    # A bare literal only ever takes an *integer* type from its peer, so
    # it stays an i32 here and the mismatch with bool is still caught.
    # This is what will let a future `2 * matrix` resolve through an impl
    # on i32 rather than the literal wrongly becoming the peer's type.
    src = """
    pub fn main() i32 {
        let x = 1 + true;
        return 0;
    }
    """
    with pytest.raises(errors.IncompatibleBinOpArgTypsError):
        util.compile_str(tmp_path, src)


# --- Evaluation order is left-to-right ---


def test_binop_evaluation_order_is_left_to_right(tmp_path):
    # Lowering is plain left-to-right, so the literal is lowered first
    # here; it emits nothing, so the call still happens exactly once.
    src = """
    extern fn puts(s: *u8) i32;
    fn a() u8 { puts("a"); return 1u8; }
    pub fn main() i32 {
        let x = 7 + a();
        return 0;
    }
    """
    util.check_prog_output(tmp_path, src, "a\n", 0)


def test_binop_double_negation_is_evaluated_before_rhs(tmp_path):
    # A doubly-negated literal is still treated as a flexible literal by
    # TypCheck, but lowering only folds a single negation, so the left
    # operand emits an overflow check before the right-hand call runs.
    # Left-to-right evaluation means the overflow panic must happen first.
    src = """
    extern fn puts(s: *u8) i32;
    fn a() i32 { puts("a"); return 5; }
    pub fn main() i32 {
        let x = - -2147483648 + a();
        return 0;
    }
    """
    llir = util.compile_modules(tmp_path, main=src)
    proc = util.link_and_run(tmp_path, llir)
    assert proc.returncode == -signal.SIGABRT
    assert proc.stdout.startswith("integer overflow\n")
    # The side effect would precede the panic if the right operand ran
    # first; only ignore the crash backtrace that follows the panic text.
    assert "a\n" not in proc.stdout.split("integer overflow\n", 1)[0]
