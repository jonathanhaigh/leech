from collections.abc import Callable, Sequence
import operator
from pprint import pformat
from typing import Any, Optional


def _message_str(message: Optional[str]) -> str:
    return "" if message is not None else f"\n{message}"


def _assert_op(
    op: Callable[[Any, Any], bool],
    op_name: str,
    lhs: Any,
    rhs: Any,
    message: Optional[str] = None,
) -> None:
    assert op(lhs, rhs), (
        f"not(lhs {op_name} rhs)\nlhs={pformat(lhs)}\nrhs={pformat(rhs)}{_message_str(message)}"
    )


def assert_eq(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    _assert_op(operator.eq, "==", lhs, rhs, message)


def assert_gt(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    _assert_op(operator.gt, ">", lhs, rhs, message)


def assert_ge(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    _assert_op(operator.ge, ">=", lhs, rhs, message)


def assert_lt(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    _assert_op(operator.lt, "<", lhs, rhs, message)


def assert_le(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    _assert_op(operator.le, "<=", lhs, rhs, message)


def assert_all_eq(
    seq: Sequence[Any], value: Any, message: Optional[str] = None
) -> None:
    for i, elt in enumerate(seq):
        assert elt == value, (
            f"seq[{i}] != value"
            f"\nseq[{i}]={pformat(elt)}"
            f"\nvalue={pformat(value)}"
            f"\nseq={pformat(seq)}"
            f"{_message_str(message)}"
        )
