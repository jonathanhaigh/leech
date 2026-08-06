# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Source file and source-span types used for diagnostics."""

import functools
import pathlib
from typing import Final

import lark.tree


class SrcFile:
    """A source file whose contents are loaded lazily."""

    path: Final[pathlib.Path]

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path

    @functools.cached_property
    def src(self) -> str:
        """The full contents of the file, read and cached on first access."""
        with self.path.open(encoding="utf-8") as f:
            return f.read()

    @functools.cached_property
    def lines(self) -> tuple[str, ...]:
        """The contents of the file split into individual lines."""
        return tuple(self.src.splitlines())


class SrcSpan:
    """A contiguous source range with zero-based offsets and one-based lines and columns."""

    file: Final[SrcFile]
    start: Final[int]
    end: Final[int]
    start_line: Final[int]
    end_line: Final[int]
    start_col: Final[int]
    end_col: Final[int]

    def __init__(
        self,
        file: SrcFile,
        start: int,
        end: int,
        start_line: int,
        end_line: int,
        start_col: int,
        end_col: int,
    ) -> None:
        self.file = file
        self.start = start
        self.end = end
        self.start_line = start_line
        self.end_line = end_line
        self.start_col = start_col
        self.end_col = end_col

    @classmethod
    def from_lark_meta(cls, file: SrcFile, lark_meta: lark.tree.Meta) -> SrcSpan:
        """Construct a span from Lark position metadata."""
        return cls(
            file,
            lark_meta.start_pos,
            lark_meta.end_pos,
            lark_meta.line,
            lark_meta.end_line,
            lark_meta.column,
            lark_meta.end_column,
        )

    @classmethod
    def single_char(cls, file: SrcFile, pos: int, line: int, col: int) -> SrcSpan:
        """Construct a one-character span from a zero-based offset and 1-based position."""
        return cls(file, pos, pos + 1, line, line, col, col + 1)
