# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Rendering diagnostics as plain text."""

import sys
from collections.abc import Sequence
from typing import Optional

from leech import asserts, diag, src


class TextRenderer:
    """Renders diagnostics as plain text, with a source excerpt, to stderr."""

    def display_diags(self, diags: Sequence[diag.Diag]) -> None:
        """Render each diagnostic in order."""
        for d in diags:
            self._display_diag(d)

    def display_internal_error(self, diags: Sequence[diag.Diag], err: Exception, tool: str) -> None:
        """Render the diagnostics found before ``tool`` crashed with ``err``, then report the
        crash as a bug in ``tool``."""
        self.display_diags(diags)
        self._display_message(
            diag.ERROR, f"internal compiler error: {type(err).__name__}: {err}", None
        )
        self._display_message(
            diag.NOTE,
            f"this is a bug in {tool}; please report it with the program that triggered it",
            None,
        )

    def _display_diag(self, d: diag.Diag) -> None:
        # Labels are shown as spanned notes.
        self._display_message(d.level, d.msg.text(), d.span)
        if d.primary_label is not None:
            self._display_message(diag.NOTE, d.primary_label.text(), d.span)
        for label in d.labels:
            self._display_message(diag.NOTE, label.msg.text(), label.span)
        for note in d.notes:
            self._display_message(diag.NOTE, note.msg.text(), note.span)

    def _display_message(self, level: diag.Level, text: str, span: Optional[src.SrcSpan]) -> None:
        print(f"{level.name}: {text}", file=sys.stderr)
        if span is not None:
            line_num_width = len(str(len(span.file.lines)))
            self._display_line(span, line_num_width)
            self._display_col_pos(span, line_num_width)

    def _display_line(self, span: src.SrcSpan, line_num_width) -> None:
        asserts.assert_gt(span.start_line, 0)
        line = span.file.lines[span.start_line - 1]
        print(f"{span.start_line:{line_num_width}d}| {line}", file=sys.stderr)

    def _display_col_pos(self, span: src.SrcSpan, line_num_width) -> None:
        prefix = "-" * (span.start_col + line_num_width + 1)
        print(f"{prefix}^", file=sys.stderr)
