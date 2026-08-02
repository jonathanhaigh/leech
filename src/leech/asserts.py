# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Assertion helpers that render both operands on failure.

Each function below raises :class:`AssertionError` with a message that
includes a pretty-printed dump of the operand(s), which is more useful for
debugging than the bare condition a plain ``assert`` would report.
"""

import operator
import pprint
from collections.abc import Callable, Sequence
from typing import Any, Optional


def _message_str(message: Optional[str]) -> str:
    return "" if message is None else f"\n{message}"


def _assert_op(
    op: Callable[[Any, Any], bool],
    op_name: str,
    lhs: Any,
    rhs: Any,
    message: Optional[str] = None,
) -> None:
    assert op(lhs, rhs), (
        f"not(lhs {op_name} rhs)\nlhs={pprint.pformat(lhs)}\nrhs={pprint.pformat(rhs)}"
        f"{_message_str(message)}"
    )


def assert_eq(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    """Assert that ``lhs == rhs``.

    :param lhs: The left-hand operand.
    :param rhs: The right-hand operand.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If ``lhs == rhs`` is false.
    """
    _assert_op(operator.eq, "==", lhs, rhs, message)


def assert_gt(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    """Assert that ``lhs > rhs``.

    :param lhs: The left-hand operand.
    :param rhs: The right-hand operand.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If ``lhs > rhs`` is false.
    """
    _assert_op(operator.gt, ">", lhs, rhs, message)


def assert_ge(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    """Assert that ``lhs >= rhs``.

    :param lhs: The left-hand operand.
    :param rhs: The right-hand operand.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If ``lhs >= rhs`` is false.
    """
    _assert_op(operator.ge, ">=", lhs, rhs, message)


def assert_lt(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    """Assert that ``lhs < rhs``.

    :param lhs: The left-hand operand.
    :param rhs: The right-hand operand.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If ``lhs < rhs`` is false.
    """
    _assert_op(operator.lt, "<", lhs, rhs, message)


def assert_le(lhs: Any, rhs: Any, message: Optional[str] = None) -> None:
    """Assert that ``lhs <= rhs``.

    :param lhs: The left-hand operand.
    :param rhs: The right-hand operand.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If ``lhs <= rhs`` is false.
    """
    _assert_op(operator.le, "<=", lhs, rhs, message)


def checked_cast[T](obj: Any, typ: type[T], message: Optional[str] = None) -> T:
    """Assert that ``obj`` is an instance of ``typ`` and return it as such.

    Useful for narrowing a value's static type after a runtime check, in
    places where an ``isinstance`` check alone wouldn't narrow the type
    checker's view (e.g. when ``typ`` is generic).

    :param obj: The value to check.
    :param typ: The type ``obj`` is expected to be an instance of.
    :param message: An optional extra message to append on failure.
    :return: ``obj``, statically typed as ``T``.
    :raises AssertionError: If ``obj`` is not an instance of ``typ``.
    """
    assert isinstance(obj, typ), (
        f"not(isinstance(obj, typ))"
        f"\nobj={pprint.pformat(obj)}"
        f"\n{type(obj)=}"
        f"\n{typ=}"
        f"{_message_str(message)}"
    )
    return obj


def assert_all_eq(seq: Sequence[Any], value: Any, message: Optional[str] = None) -> None:
    """Assert that every element of ``seq`` equals ``value``.

    :param seq: The sequence to check.
    :param value: The value each element of ``seq`` is expected to equal.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If any element of ``seq`` does not equal
        ``value``.
    """
    for i, elt in enumerate(seq):
        assert elt == value, (
            f"seq[{i}] != value"
            f"\nseq[{i}]={pprint.pformat(elt)}"
            f"\nvalue={pprint.pformat(value)}"
            f"\nseq={pprint.pformat(seq)}"
            f"{_message_str(message)}"
        )


def assert_in(obj: Any, container: Any, message: Optional[str] = None) -> None:
    """Assert that ``obj in container``.

    :param obj: The value to look for.
    :param container: The container to search.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If ``obj`` is not in ``container``.
    """
    assert obj in container, (
        f"not(obj in container)"
        f"\nobj={pprint.pformat(obj)}"
        f"\ncontainer={pprint.pformat(container)}"
        f"{_message_str(message)}"
    )


def assert_not_in(obj: Any, container: Any, message: Optional[str] = None) -> None:
    """Assert that ``obj not in container``.

    :param obj: The value to look for.
    :param container: The container to search.
    :param message: An optional extra message to append on failure.
    :raises AssertionError: If ``obj`` is in ``container``.
    """
    assert obj not in container, (
        f"not(obj not in container)"
        f"\nobj={pprint.pformat(obj)}"
        f"\ncontainer={pprint.pformat(container)}"
        f"{_message_str(message)}"
    )
