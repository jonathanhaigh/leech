import pytest

from util import check_prog_output


@pytest.mark.parametrize(
    "op,lhs,rhs,result",
    (
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
    ),
)
def test_cmp(op, lhs, rhs, result, tmp_path):
    src = f"""
    pub fn main() i32 {{
        return if ({lhs} {op} {rhs}) {{ 100 }} else {{ 200 }};
    }}
    """
    expected_status = 100 if result else 200
    check_prog_output(tmp_path, src, "", expected_status)
