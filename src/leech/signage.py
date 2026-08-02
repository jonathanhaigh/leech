# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Integer signedness.

Pulled out of :mod:`leech.typs` because :mod:`leech.ast` needs it too (to
parse an integer literal's ``i``/``u`` suffix), and ``ast`` and ``typs``
otherwise need each other's real classes at runtime - sharing this leaf
module instead avoids a genuine import cycle between them for the sake of
one enum.
"""

import enum


class Signage(enum.Enum):
    """Whether an integer type is signed or unsigned."""

    SIGNED = 0
    UNSIGNED = 1


SIGNED = Signage.SIGNED
UNSIGNED = Signage.UNSIGNED
