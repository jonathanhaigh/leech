# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Integer signedness."""

import enum


class Signage(enum.Enum):
    """Whether an integer type is signed or unsigned."""

    SIGNED = 0
    UNSIGNED = 1


SIGNED = Signage.SIGNED
UNSIGNED = Signage.UNSIGNED
