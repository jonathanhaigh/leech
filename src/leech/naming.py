# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Generation of unique, human-readable names."""

from typing import Final


class VarNamer:
    """Generate unique names by disambiguating repeated basenames."""

    _counters: Final[dict[str, int]]

    def __init__(self) -> None:
        self._counters = {}

    def __call__(self, basename: str) -> str:
        """Return ``basename`` once, then append an incrementing ``.N`` suffix."""
        counter = self._counters.setdefault(basename, 1)
        self._counters[basename] += 1
        if counter == 1:
            return basename
        return f"{basename}.{counter}"
