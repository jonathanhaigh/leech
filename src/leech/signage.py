"""Integer signedness and the target address width.

Pulled out of :mod:`leech.typs` because :mod:`leech.ast` needs these
constants too (to parse an integer literal's ``i``/``u`` suffix), and
``ast`` and ``typs`` otherwise need each other's real classes at runtime -
sharing this leaf module instead avoids a genuine import cycle between
them for the sake of three constants.
"""

from enum import Enum


class Signage(Enum):
    """Whether an integer type is signed or unsigned."""

    SIGNED = 0
    UNSIGNED = 1


SIGNED = Signage.SIGNED
UNSIGNED = Signage.UNSIGNED

ADDR_SIZE = 64
"""Bit width of ``usize``/``isize`` on the target platform."""
