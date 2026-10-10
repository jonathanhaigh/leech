# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""User-facing diagnostics: error/warning types, and their rendering."""

import dataclasses
import pathlib
import sys
from collections.abc import Sequence
from typing import ClassVar, Final, Optional

from leech import asserts, diag, diag_kinds, src

Level = diag.Level
NOTE = diag.NOTE
WARNING = diag.WARNING
ERROR = diag.ERROR


@dataclasses.dataclass(frozen=True)
class Message:
    """A single diagnostic message, optionally located in source."""

    level: Level
    message: str
    span: Optional[src.SrcSpan]


class UserError(Exception):
    """A diagnostic with a primary message and optional accompanying messages.

    ``kind`` is the diagnostic's entry in the catalogue.
    """

    kind: ClassVar[diag.DiagKind]
    message: Final[Message]
    extra: Final[list[Message]]

    def __init__(self, level: Level, message: str, span: Optional[src.SrcSpan]) -> None:
        super().__init__(message)
        self.message = Message(level, message, span)
        self.extra = []

    def _add_extra(self, level: Level, message: str, span: Optional[src.SrcSpan]) -> None:
        self.extra.append(Message(level, message, span))

    @property
    def level(self):
        """The severity of this error's primary message."""
        return self.message.level

    @property
    def span(self) -> Optional[src.SrcSpan]:
        """The primary message's location."""
        return self.message.span


class CcInvalidError(UserError):
    """Raised when the ``CC`` environment variable can't be split into a command."""

    kind = diag_kinds.MALFORMED_C_COMPILER_COMMAND

    def __init__(self, value: str, reason: str) -> None:
        super().__init__(ERROR, f"The C compiler command CC={value!r} is invalid: {reason}", None)


class CcNotFoundError(UserError):
    """Raised when the C compiler used for linking isn't on ``PATH``."""

    kind = diag_kinds.MISSING_C_COMPILER

    def __init__(self, program: str) -> None:
        super().__init__(
            ERROR,
            f'C compiler "{program}" not found; install gcc or clang, or set CC to a C compiler',
            None,
        )


class LinkFailedError(UserError):
    """Raised when the C compiler fails to link an executable; notes carry its output."""

    kind = diag_kinds.LINK_FAILURE

    def __init__(self, command: str, problem: str, output: str) -> None:
        super().__init__(ERROR, f"Linking failed: `{command}` {problem}", None)
        if output.strip():
            self._add_extra(NOTE, output.rstrip("\n"), None)


class BuildOutputError(UserError):
    """Raised when a build's output files can't be written."""

    kind = diag_kinds.UNWRITABLE_OUTPUT

    def __init__(self, reason: str) -> None:
        super().__init__(ERROR, f"Cannot write build output: {reason}", None)


class RunFailedError(UserError):
    """Raised when a built program can't be started."""

    kind = diag_kinds.RUN_FAILURE

    def __init__(self, exe: pathlib.Path, reason: str) -> None:
        super().__init__(ERROR, f"Cannot run {exe}: {reason}", None)


class DoctorCheckError(UserError):
    """Raised when a ``leech doctor`` toolchain check fails; a note suggests a fix."""

    kind = diag_kinds.TOOLCHAIN_CHECK_FAILURE

    def __init__(self, problem: str, fix: str) -> None:
        super().__init__(ERROR, problem, None)
        self._add_extra(NOTE, fix, None)


class DoctorFixNote(UserError):  # noqa: N818 - a note, not an error
    """A note from ``leech doctor`` suggesting how to fix the problems reported before it."""

    kind = diag_kinds.C_COMPILER_HINT

    def __init__(self, fix: str) -> None:
        super().__init__(NOTE, fix, None)


class TextErrorRenderer:
    """Renders diagnostics as plain text, with a source excerpt, to stderr."""

    def display_errors(self, errs: Sequence[diag.AnyDiag]) -> None:
        """Render each error in order."""
        for err in errs:
            self._display_error(err)

    def display_internal_error(
        self, errs: Sequence[diag.AnyDiag], err: Exception, tool: str
    ) -> None:
        """Render the diagnostics found before ``tool`` crashed with ``err``, then report the
        crash as a bug in ``tool``."""
        self.display_errors(errs)
        self._display_message(
            Message(ERROR, f"internal compiler error: {type(err).__name__}: {err}", None)
        )
        self._display_message(
            Message(
                NOTE,
                f"this is a bug in {tool}; please report it with the program that triggered it",
                None,
            )
        )

    def _display_error(self, err: diag.AnyDiag) -> None:
        match err:
            case diag.Diag():
                # Labels are shown as spanned notes.
                self._display_message(Message(err.level, err.msg.text(), err.span))
                if err.primary_label is not None:
                    self._display_message(Message(NOTE, err.primary_label.text(), err.span))
                for label in err.labels:
                    self._display_message(Message(NOTE, label.msg.text(), label.span))
                for note in err.notes:
                    self._display_message(Message(NOTE, note.msg.text(), note.span))
            case UserError():
                self._display_message(err.message)
                for message in err.extra:
                    self._display_message(message)

    def _display_message(self, message: Message) -> None:
        print(f"{message.level.name}: {message.message}", file=sys.stderr)
        if message.span is not None:
            line_num_width = len(str(len(message.span.file.lines)))
            self._display_line(message.span, line_num_width)
            self._display_col_pos(message.span, line_num_width)

    def _display_line(self, span: src.SrcSpan, line_num_width) -> None:
        asserts.assert_gt(span.start_line, 0)
        line = span.file.lines[span.start_line - 1]
        print(f"{span.start_line:{line_num_width}d}| {line}", file=sys.stderr)

    def _display_col_pos(self, span: src.SrcSpan, line_num_width) -> None:
        prefix = "-" * (span.start_col + line_num_width + 1)
        print(f"{prefix}^", file=sys.stderr)
