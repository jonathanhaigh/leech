# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Source file and source-span types used for diagnostics."""

import functools
import pathlib
from typing import Final

import lark.tree


class SrcFile:
    """A source file on disk, with its contents loaded lazily.

    :param path: The path to the source file.
    """

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
    """A contiguous range of source text within a :class:`SrcFile`.

    Used to attach source locations to diagnostics.

    :param file: The source file the span is located in.
    :param start: The span's start position as a character offset.
    :param end: The span's end position as a character offset.
    :param start_line: The span's 1-based start line number.
    :param end_line: The span's 1-based end line number.
    :param start_col: The span's 1-based start column number.
    :param end_col: The span's 1-based end column number.
    """

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
        """Construct a span from a Lark ``Meta``/``Token``'s position fields.

        :param file: The source file the span is located in.
        :param lark_meta: The Lark parse-tree metadata to derive the span's
            position from.
        :return: The equivalent span.
        """
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
        """Construct a span covering a single character at a raw position.

        Used when only a bare position is available (e.g. from a lexer
        error that couldn't form a token at all), rather than a full Lark
        ``Meta``/``Token`` with both a start and an end.

        :param file: The source file the span is located in.
        :param pos: The character's index into the file.
        :param line: The character's 1-based line number.
        :param col: The character's 1-based column number.
        :return: A one-character-wide span starting at ``pos``.
        """
        return cls(file, pos, pos + 1, line, line, col, col + 1)
