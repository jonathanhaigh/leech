"""Helper functions for working with ``Optional`` values."""

from collections.abc import Callable


def opt_map[T, R](o: T | None, fn: Callable[[T], R]) -> R | None:
    """Apply ``fn`` to ``o`` if it is present.

    :param o: The optional value.
    :param fn: The function to apply to ``o`` if it is not ``None``.
    :return: ``fn(o)`` if ``o`` is not ``None``, otherwise ``None``.
    """
    if o is None:
        return None
    return fn(o)


def opt_or_else[T](o: T | None, fn: Callable[[], T]) -> T:
    """Return ``o`` if present, otherwise the result of calling ``fn``.

    :param o: The optional value.
    :param fn: The function to call to produce a fallback value if ``o``
        is ``None``.
    :return: ``o`` if it is not ``None``, otherwise ``fn()``.
    """
    if o is not None:
        return o
    return fn()


def opt_or_default[T](o: T | None, v: T) -> T:
    """Return ``o`` if present, otherwise a default value.

    :param o: The optional value.
    :param v: The fallback value to use if ``o`` is ``None``.
    :return: ``o`` if it is not ``None``, otherwise ``v``.
    """
    if o is not None:
        return o
    return v


def opt_unwrap[T](o: T | None) -> T:
    """Assert that ``o`` is present and return it.

    :param o: The optional value, which must not be ``None``.
    :return: ``o``.
    :raises AssertionError: If ``o`` is ``None``.
    """
    assert o is not None
    return o
