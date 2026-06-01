from collections.abc import Callable
from typing import Optional


def opt_map[T, R](o: Optional[T], fn: Callable[[T], R]) -> Optional[R]:
    if o is None:
        return None
    return fn(o)


def opt_or_else[T](o: Optional[T], fn: Callable[[], T]) -> T:
    if o is not None:
        return o
    return fn()


def opt_or_default[T](o: Optional[T], v: T) -> T:
    if o is not None:
        return o
    return v


def opt_unwrap[T](o: Optional[T]) -> T:
    assert o is not None
    return o
