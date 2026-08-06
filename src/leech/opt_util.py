# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Helper functions for working with ``Optional`` values."""

from collections.abc import Callable
from typing import Optional


def opt_map[T, R](o: Optional[T], fn: Callable[[T], R]) -> Optional[R]:
    """Return ``fn(o)`` when ``o`` is present, otherwise ``None``."""
    if o is None:
        return None
    return fn(o)


def opt_or_else[T](o: Optional[T], fn: Callable[[], T]) -> T:
    """Return ``o`` when present, otherwise ``fn()``."""
    if o is not None:
        return o
    return fn()


def opt_or_default[T](o: Optional[T], v: T) -> T:
    """Return ``o`` when present, otherwise ``v``."""
    if o is not None:
        return o
    return v


def opt_unwrap[T](o: Optional[T]) -> T:
    """Assert that ``o`` is present and return it."""
    assert o is not None
    return o
