import pytest

from util import check_prog_output

CMP_CASES = (
    ("<", 1, 2, True),
    ("<", 2, 1, False),
    ("<", 1, 1, False),
    ("<=", 1, 2, True),
    ("<=", 2, 1, False),
    ("<=", 1, 1, True),
    ("==", 1, 2, False),
    ("==", 2, 1, False),
    ("==", 1, 1, True),
    (">=", 1, 2, False),
    (">=", 2, 1, True),
    (">=", 1, 1, True),
    (">", 1, 2, False),
    (">", 2, 1, True),
    (">", 1, 1, False),
)


@pytest.mark.parametrize("op,lhs,rhs,result", CMP_CASES)
def test_cmp(op, lhs, rhs, result, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if ({lhs} {op} {rhs}) {{ 100 }} else {{ 200 }};
    }}
    """
    expected_status = 100 if result else 200
    check_prog_output(tmp_path, src, "", expected_status)


@pytest.mark.parametrize("op,lhs,rhs,result", CMP_CASES)
def test_comptime_cmp(op, lhs, rhs, result, tmp_path):
    src = f"""
    let x = if ({lhs} {op} {rhs}) {{ 100 }} else {{ 200 }};
    pub fn main() i32 {{
        return x;
    }}
    """
    expected_status = 100 if result else 200
    check_prog_output(tmp_path, src, "", expected_status)
