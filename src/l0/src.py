from functools import cached_property
from pathlib import Path

from lark.tree import Meta


class SrcFile:
    path: Path

    def __init__(self, path: Path) -> None:
        self.path = path

    @cached_property
    def src(self) -> str:
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()

    @cached_property
    def lines(self) -> list[str]:
        return self.src.splitlines()


class SrcSpan:
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
