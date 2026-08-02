"""Generation of unique, human-readable names."""

from typing import Final


class VarNamer:
    """Generates unique names by disambiguating repeated basenames.

    Each instance keeps its own counters, so the same basename yields a
    fresh, uniquely-suffixed name every time it is reused.
    """

    _counters: Final[dict[str, int]]

    def __init__(self) -> None:
        self._counters = {}

    def __call__(self, basename: str) -> str:
        """Return a unique name derived from ``basename``.

        The first call for a given ``basename`` returns it unchanged;
        subsequent calls append an incrementing ``.N`` suffix.

        :param basename: The name to disambiguate.
        :return: ``basename``, or ``basename`` suffixed with ``.N`` if it
            has already been used on this instance.
        """
        counter = self._counters.setdefault(basename, 1)
        self._counters[basename] += 1
        if counter == 1:
            return basename
        return f"{basename}.{counter}"
