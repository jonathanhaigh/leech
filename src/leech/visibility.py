# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Whether declared items are visible outside their own module."""

import enum
from typing import Optional

from leech import asserts, ast


class Access(enum.Enum):
    """Whether a module item is visible outside its declaring module."""

    PRIVATE = 0
    PUBLIC = 1

    @staticmethod
    def from_ast(pub_ast: Optional[ast.Access]) -> Access:
        """Return public for a parsed ``pub`` keyword and private otherwise."""
        if pub_ast is None:
            return PRIVATE
        asserts.assert_eq(pub_ast.value, "pub")
        return PUBLIC


PRIVATE = Access.PRIVATE
PUBLIC = Access.PUBLIC
