import pytest
from l0.l0errors import IncompatibleBinOpArgTypsError, InvalidBinOpArgTypError
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
        x = {lhs} + {rhs};
        return 0;
    }}
    """
    with pytest.raises(IncompatibleBinOpArgTypsError):
        compile_str(tmp_path, src)


def test_invalid_bin_op_arg(tmp_path):
    src = """
    pub fn main() i32 {
        x = true + false;
        return 0;
    }
    """
    with pytest.raises(InvalidBinOpArgTypError):
        compile_str(tmp_path, src)
