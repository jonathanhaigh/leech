"""Source file and source-span types used for diagnostics."""

from functools import cached_property
from pathlib import Path

from lark.tree import Meta


class SrcFile:
    """A source file on disk, with its contents loaded lazily.

    :param path: The path to the source file.
    """

    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path

    @cached_property
    def src(self) -> str:
        """The full contents of the file, read and cached on first access."""
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()

    @cached_property
    def lines(self) -> list[str]:
        """The contents of the file split into individual lines."""
        return self.src.splitlines()


class SrcSpan:
    """A contiguous range of source text within a :class:`SrcFile`.

    Used to attach source locations to diagnostics.

    :param file: The source file the span is located in.
    :param lark_meta: The Lark parse-tree metadata to derive the span's
        position from.
    """

    file: SrcFile
    start: int
    end: int
    start_line: int
    end_line: int
    start_col: int
    end_col: int

    def __init__(self, file: SrcFile, lark_meta: Meta) -> None:
        self.file = file
        self.start = lark_meta.start_pos
        self.end = lark_meta.end_pos
        self.start_line = lark_meta.line
        self.end_line = lark_meta.end_line
        self.start_col = lark_meta.column
        self.end_col = lark_meta.end_column
