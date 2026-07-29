import pytest
from util import check_prog_output, compile_str

from leech.errors import InvalidBinOpArgTypError, InvalidUnaryOpArgTypError

AND_CASES = (
    (True, True, True),
    (True, False, False),
    (False, True, False),
    (False, False, False),
)

OR_CASES = (
    (True, True, True),
    (True, False, True),
    (False, True, True),
    (False, False, False),
)

NOT_CASES = (
    (True, False),
    (False, True),
)


def _lit(b: bool) -> str:
    return "true" if b else "false"


@pytest.mark.parametrize("lhs,rhs,result", AND_CASES)
def test_and(lhs, rhs, result, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if ({_lit(lhs)} and {_lit(rhs)}) {{ 100 }} else {{ 200 }};
    }}
    """
    check_prog_output(tmp_path, src, "", 100 if result else 200)


@pytest.mark.parametrize("lhs,rhs,result", AND_CASES)
def test_comptime_and(lhs, rhs, result, tmp_path):
    src = f"""
    let x = if ({_lit(lhs)} and {_lit(rhs)}) {{ 100 }} else {{ 200 }};
    pub fn main() i32 {{
        return x;
    }}
    """
    check_prog_output(tmp_path, src, "", 100 if result else 200)


@pytest.mark.parametrize("lhs,rhs,result", OR_CASES)
def test_or(lhs, rhs, result, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if ({_lit(lhs)} or {_lit(rhs)}) {{ 100 }} else {{ 200 }};
    }}
    """
    check_prog_output(tmp_path, src, "", 100 if result else 200)


@pytest.mark.parametrize("lhs,rhs,result", OR_CASES)
def test_comptime_or(lhs, rhs, result, tmp_path):
    src = f"""
    let x = if ({_lit(lhs)} or {_lit(rhs)}) {{ 100 }} else {{ 200 }};
    pub fn main() i32 {{
        return x;
    }}
    """
    check_prog_output(tmp_path, src, "", 100 if result else 200)


@pytest.mark.parametrize("operand,result", NOT_CASES)
def test_not(operand, result, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if (not {_lit(operand)}) {{ 100 }} else {{ 200 }};
    }}
    """
    check_prog_output(tmp_path, src, "", 100 if result else 200)


@pytest.mark.parametrize("operand,result", NOT_CASES)
def test_comptime_not(operand, result, tmp_path):
    src = f"""
    let x = if (not {_lit(operand)}) {{ 100 }} else {{ 200 }};
    pub fn main() i32 {{
        return x;
    }}
    """
    check_prog_output(tmp_path, src, "", 100 if result else 200)


def test_not_binds_tighter_than_and(tmp_path):
    # not false and false == (not false) and false == true and false == false
    src = """
    pub fn main() i32 {
        return if (not false and false) { 100 } else { 200 };
    }
    """
    check_prog_output(tmp_path, src, "", 200)


def test_and_binds_tighter_than_or(tmp_path):
    # If `and` binds tighter (correct): true or (false and false) == true.
    # If `or` bound tighter instead: (true or false) and false == false.
    # The two groupings must disagree for this to actually test precedence.
    src = """
    pub fn main() i32 {
        return if (true or false and false) { 100 } else { 200 };
    }
    """
    check_prog_output(tmp_path, src, "", 100)


def test_logic_ops_bind_looser_than_comparison(tmp_path):
    # not 1 == 2  ==  not (1 == 2)  ==  not false  ==  true
    src = """
    pub fn main() i32 {
        return if (not 1 == 2) { 100 } else { 200 };
    }
    """
    check_prog_output(tmp_path, src, "", 100)


# --- Short-circuiting is actually observed, not assumed ---


def test_and_short_circuits_at_runtime(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    fn side_effect() bool {
        puts("side effect");
        return true;
    }
    pub fn main() i32 {
        let f = false;
        let x = f and side_effect();
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_and_evaluates_rhs_when_lhs_true(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    fn side_effect() bool {
        puts("side effect");
        return true;
    }
    pub fn main() i32 {
        let t = true;
        let x = t and side_effect();
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "side effect\n", 0)


def test_or_short_circuits_at_runtime(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    fn side_effect() bool {
        puts("side effect");
        return true;
    }
    pub fn main() i32 {
        let t = true;
        let x = t or side_effect();
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_or_evaluates_rhs_when_lhs_false(tmp_path):
    src = """
    extern fn puts(s: *u8) i32;
    fn side_effect() bool {
        puts("side effect");
        return true;
    }
    pub fn main() i32 {
        let f = false;
        let x = f or side_effect();
        return 0;
    }
    """
    check_prog_output(tmp_path, src, "side effect\n", 0)


def test_comptime_and_short_circuits(tmp_path):
    # Without short-circuiting, the interpreter would evaluate the
    # division and raise DivisionByZeroAtComptimeError.
    src = """
    let x = false and (1i32 / 0i32 == 0i32);
    pub fn main() i32 {
        return if (x) { 1 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 0)


def test_comptime_or_short_circuits(tmp_path):
    src = """
    let x = true or (1i32 / 0i32 == 0i32);
    pub fn main() i32 {
        return if (x) { 1 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 1)


# --- Type errors ---


@pytest.mark.parametrize("op", ("and", "or"))
def test_invalid_logic_bin_op_lhs(op, tmp_path):
    src = f"""
    pub fn main() i32 {{
        let x = 1 {op} true;
        return 0;
    }}
    """
    with pytest.raises(InvalidBinOpArgTypError):
        compile_str(tmp_path, src)


@pytest.mark.parametrize("op", ("and", "or"))
def test_invalid_logic_bin_op_rhs(op, tmp_path):
    src = f"""
    pub fn main() i32 {{
        let x = true {op} 1;
        return 0;
    }}
    """
    with pytest.raises(InvalidBinOpArgTypError):
        compile_str(tmp_path, src)


def test_invalid_not_operand(tmp_path):
    src = """
    pub fn main() i32 {
        let x = not 1;
        return 0;
    }
    """
    with pytest.raises(InvalidUnaryOpArgTypError):
        compile_str(tmp_path, src)


@pytest.mark.parametrize("op", ("and", "or"))
def test_never_lhs_compiles(op, tmp_path):
    # A diverging left operand makes the whole and/or expression diverge
    # too - symmetric with the already-working diverging-rhs case below.
    src = f"""
    fn f() i32 {{
        let a = ({{ return 1; }}) {op} true;
        return 0;
    }}
    pub fn main() i32 {{
        return f();
    }}
    """
    check_prog_output(tmp_path, src, "", 1)


@pytest.mark.parametrize(
    "op,lhs",
    (
        ("and", "true"),  # `and` only evaluates its rhs when the lhs is true
        ("or", "false"),  # `or` only evaluates its rhs when the lhs is false
    ),
)
def test_never_rhs_compiles(op, lhs, tmp_path):
    src = f"""
    fn f() i32 {{
        let a = {lhs} {op} ({{ return 1; }});
        return 0;
    }}
    pub fn main() i32 {{
        return f();
    }}
    """
    check_prog_output(tmp_path, src, "", 1)


def test_deeply_nested_logic_ops(tmp_path):
    # Regression coverage for correct block-compile ordering: a
    # short-circuit's direct edge into its merge block, chained several
    # levels deep, must not be compiled before the (deeper) block that
    # produces the merge's other incoming value.
    src = """
    pub fn main() i32 {
        let a = true;
        let b = false;
        let c = true;
        let d = false;
        let e = true;
        return if (a and (b or (c and (d or e)))) { 7 } else { 0 };
    }
    """
    check_prog_output(tmp_path, src, "", 7)
