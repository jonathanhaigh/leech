# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

import pytest

from leech import diag, diag_kinds

CMP_OPS = ("<", "<=", "==", "!=", ">=", ">")

MISMATCHED_TYP_PAIRS = (
    ("0u8", "0i8"),
    ("0i10", "0i11"),
    ("0u10", "0u11"),
)

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
    ("!=", 1, 2, True),
    ("!=", 2, 1, True),
    ("!=", 1, 1, False),
    (">=", 1, 2, False),
    (">=", 2, 1, True),
    (">=", 1, 1, True),
    (">", 1, 2, False),
    (">", 2, 1, True),
    (">", 1, 1, False),
)


@pytest.mark.parametrize("op,lhs,rhs,result", CMP_CASES)
def test_cmp(op, lhs, rhs, result, compiler):
    src = f"""
    pub fn main() i32 {{
        return if ({lhs} {op} {rhs}) {{ 100 }} else {{ 200 }};
    }}
    """
    expected_status = 100 if result else 200
    compiler.check(src, exit_status=expected_status)


@pytest.mark.parametrize("op,lhs,rhs,result", CMP_CASES)
def test_comptime_cmp(op, lhs, rhs, result, compiler):
    src = f"""
    let x = if ({lhs} {op} {rhs}) {{ 100 }} else {{ 200 }};
    pub fn main() i32 {{
        return x;
    }}
    """
    expected_status = 100 if result else 200
    compiler.check(src, exit_status=expected_status)


@pytest.mark.parametrize("op", CMP_OPS)
@pytest.mark.parametrize("lhs,rhs", MISMATCHED_TYP_PAIRS)
def test_incompatible_cmp_args(compiler, op, lhs, rhs):
    src = f"""
    pub fn main() i32 {{
        let x = {lhs} {op} {rhs};
        return 0;
    }}
    """
    with pytest.raises(diag.CompilationError) as exc_info:
        compiler.compile(src)
    assert exc_info.value.kinds == (diag_kinds.CONFLICTING_OPERAND_TYPES,)
