# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Per-compilation collection of diagnostics, and proof that an error was reported."""

import enum
from collections.abc import Hashable
from typing import TYPE_CHECKING, Final, Optional

if TYPE_CHECKING:
    from leech import errors


class Level(enum.IntEnum):
    """The severity of a diagnostic message."""

    NOTE = 0
    WARNING = 1
    ERROR = 2


NOTE = Level.NOTE
WARNING = Level.WARNING
ERROR = Level.ERROR

_KEY: Final = object()
"""Guards ``ReportProof``'s constructor, so only ``Diags`` creates proofs."""


class ReportProof:
    """Proof that an error diagnostic has been reported to a ``Diags``.

    Only ``Diags`` can create one. Code that needs to fail or recover without reporting
    anything itself requires a proof, so it cannot fail silently. ``diag`` is the reported
    error.
    """

    __slots__ = ("diag",)

    diag: Final[errors.UserError]

    def __init__(self, key: object, diag: errors.UserError) -> None:
        assert key is _KEY, "only Diags can create a ReportProof"
        self.diag = diag


class ReportedError(Exception):
    """Unwinds from a user error that has already been reported."""

    reported: Final[ReportProof]

    def __init__(self, reported: ReportProof) -> None:
        super().__init__("an error was reported")
        self.reported = reported


class Diags:
    """The diagnostics of one compilation, in emission order, without duplicates.

    A diagnostic duplicates an earlier one when it has the same type, levels, messages and
    source locations. Source files are compared by resolved path, so diagnostics from
    separately loaded copies of a file are duplicates too.
    """

    _diags: Final[list[errors.UserError]]
    #: Each distinct diagnostic's key, mapped to its proof if it is an error.
    _seen: Final[dict[Hashable, Optional[ReportProof]]]
    _first_error: Optional[ReportProof]

    def __init__(self) -> None:
        self._diags = []
        self._seen = {}
        self._first_error = None

    def error(self, err: errors.UserError) -> ReportProof:
        """Record the error ``err`` unless it duplicates an earlier diagnostic.

        Returns the proof of ``err``, or of the earlier diagnostic it duplicates.
        """
        assert err.level == ERROR, f"not an error: {err!r}"
        reported = self._record(err, None)
        assert reported is not None
        return reported

    def warn(self, err: errors.UserError) -> None:
        """Record the warning ``err`` unless it duplicates an earlier diagnostic."""
        assert err.level == WARNING, f"not a warning: {err!r}"
        self._record(err, None)

    def _record(
        self, err: errors.UserError, reported: Optional[ReportProof]
    ) -> Optional[ReportProof]:
        """Record ``err`` with ``reported``, its proof from another ``Diags``, if any."""
        key = _key(err)
        if key in self._seen:
            return self._seen[key]
        if err.level >= ERROR:
            if reported is None:
                reported = ReportProof(_KEY, err)
            assert reported.diag is err, "a proof must prove the diagnostic it is recorded with"
            if self._first_error is None:
                self._first_error = reported
        else:
            assert reported is None, "only errors have proofs"
        self._seen[key] = reported
        self._diags.append(err)
        return reported

    def merge(self, other: Diags) -> None:
        """Record each of ``other``'s diagnostics in its emission order, with its proof.

        A diagnostic that duplicates one already recorded keeps the earlier one's proof.
        """
        for err in other._diags:
            self._record(err, other._seen[_key(err)])

    def all(self) -> tuple[errors.UserError, ...]:
        """Every distinct diagnostic, in emission order."""
        return tuple(self._diags)

    @property
    def has_errors(self) -> bool:
        return self._first_error is not None

    @property
    def level(self) -> Level:
        """The highest level among the diagnostics, or ``NOTE`` if there are none."""
        return max((err.level for err in self._diags), default=NOTE)

    def any_error(self) -> Optional[ReportProof]:
        """The proof of the first error reported, if any."""
        return self._first_error


def _key(err: errors.UserError) -> Hashable:
    return (type(err), *(_message_key(m) for m in (err.message, *err.extra)))


def _message_key(message: errors.Message) -> Hashable:
    span = message.span
    location = None
    if span is not None:
        location = (
            span.file.path.resolve(),
            span.start_line,
            span.start_col,
            span.end_line,
            span.end_col,
        )
    return (message.level, message.message, location)
