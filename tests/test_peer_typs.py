import pytest

from leech.errors import (
    IfElsTypMismatchError,
    IncompatibleBinOpArgTypsError,
    IncompatibleTypInArrayExpr,
)
from util import check_prog_output, compile_str


# --- Array literals: a bare literal takes its type from its siblings
# wherever it sits, rather than the first element deciding for everyone ---


@pytest.mark.parametrize(
    "elements",
    (
        "1u8, 2, 3",
        "1, 2u8, 3",
        "1, 2, 3u8",
        "1u8, 2u8, 3",
    ),
)
def test_array_elt_typ_independent_of_order(elements, tmp_path):
    # takes_u8 only accepts a u8, so this compiles exactly when every
    # element came out as one.
    src = f"""
    fn takes_u8(x: u8) u8 {{ return x; }}
    pub fn main() i32 {{
        let a = [{elements}];
        return takes_u8(a[1usize]);
    }}
    """
    check_prog_output(tmp_path, src, "", 2)


def test_array_expected_elt_typ_still_wins(tmp_path):
    src = """
    pub fn main() i32 {
        let a: [u8; 3] = [1, 2, 3];
        return a[2usize];
    }
    """
    check_prog_output(tmp_path, src, "", 3)


def test_array_all_flexible_falls_back_to_i32(tmp_path):
    src = """
    fn takes_i32(x: i32) i32 { return x; }
    pub fn main() i32 {
        let a = [1, 2, 3];
        return takes_i32(a[1usize]);
    }
    """
    check_prog_output(tmp_path, src, "", 2)


@pytest.mark.parametrize("elements", ("1u8, 2u16", "1u16, 2u8"))
def test_array_two_decided_elts_must_agree(elements, tmp_path):
    # Without a declared array type there's no unambiguous target to
    # coerce towards, so elements that decide their own type have to
    # match exactly - in either order. Widening one into the other would
    # make acceptance depend on which was written first.
    src = f"""
    pub fn main() i32 {{
        let a = [{elements}];
        return 0;
    }}
    """
    with pytest.raises(IncompatibleTypInArrayExpr):
        compile_str(tmp_path, src)


@pytest.mark.parametrize("elements", ("1u8, 2u16", "1u16, 2u8"))
def test_array_elts_widen_when_the_typ_is_declared(elements, tmp_path):
    # With a declared type the target *is* unambiguous, so the usual
    # widening coercion applies and both orders are accepted.
    src = f"""
    fn takes_u32(x: u32) u32 {{ return x; }}
    pub fn main() i32 {{
        let a: [u32; 2] = [{elements}];
        return if (takes_u32(a[1usize]) == 2u32) {{ 7 }} else {{ 0 }};
    }}
    """
    check_prog_output(tmp_path, src, "", 7)


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
    check_prog_output(tmp_path, src, "", 1)


def test_if_expected_typ_still_wins(tmp_path):
    src = """
    pub fn main() i32 {
        let c = false;
        let x: u8 = if (c) { 1 } else { 2 };
        return x;
    }
    """
    check_prog_output(tmp_path, src, "", 2)


def test_if_two_decided_arms_must_agree(tmp_path):
    src = """
    pub fn main() i32 {
        let c = true;
        let x = if (c) { 1u8 } else { 2u16 };
        return 0;
    }
    """
    with pytest.raises(IfElsTypMismatchError):
        compile_str(tmp_path, src)


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
    check_prog_output(tmp_path, src, "", 2)


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
    check_prog_output(tmp_path, src, "", 11)


def test_binop_takes_typ_from_context(tmp_path):
    # Neither operand decides its own type, so both take the declared
    # type of the variable the result goes into.
    src = """
    pub fn main() i32 {
        let x: u8 = 200 + 55;
        return if (x == 255u8) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)


@pytest.mark.parametrize("expr", ("1u8 < 10", "1 < 10u8"))
def test_comparison_operand_typ_independent_of_order(expr, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if ({expr}) {{ 7 }} else {{ 0 }};
    }}
    """
    check_prog_output(tmp_path, src, "", 7)


@pytest.mark.parametrize("expr", ("1u8 + 2u16", "1u16 + 2u8"))
def test_binop_two_decided_operands_must_agree(expr, tmp_path):
    src = f"""
    pub fn main() i32 {{
        let x = {expr};
        return 0;
    }}
    """
    with pytest.raises(IncompatibleBinOpArgTypsError):
        compile_str(tmp_path, src)


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
    with pytest.raises(IncompatibleBinOpArgTypsError):
        compile_str(tmp_path, src)


# --- Deferring a flexible peer must not reorder anything observable ---


def test_array_peer_resolution_preserves_effect_order(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    fn a() u8 { puts("a"); return 1u8; }
    fn b() u8 { puts("b"); return 2u8; }
    pub fn main() i32 {
        let arr = [a(), 5, b()];
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "a\nb\n", 0)


def test_binop_peer_resolution_preserves_effect_order(tmp_path):
    # The literal is on the left, so its operand is lowered second - but
    # it emits nothing, so the call still happens exactly once, here.
    src = """
    extern fn puts(s: *u8) i32;
    fn a() u8 { puts("a"); return 1u8; }
    pub fn main() i32 {
        let x = 7 + a();
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "a\n", 0)
