# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Diagnostics: their kinds and values, their per-compilation collection, and proof that an
error was reported."""

import dataclasses
import enum
import pathlib
import string
import types
from collections.abc import Hashable, Mapping, Sequence
from typing import TYPE_CHECKING, Final, NoReturn, Optional, Protocol, Self, overload

from leech import src

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


class DiagArg(Protocol):
    """A value a diagnostic message refers to, such as a type.

    A message keeps the value itself, and renders it with ``diag_str`` only when the message
    is rendered. ``report_proof`` is the proof of an error already reported about the value,
    if there is one, so that a diagnostic about it can be recognized as a cascade.
    """

    def diag_str(self) -> str: ...

    def report_proof(self) -> Optional[ReportProof]: ...


type DiagArgValue = str | int | DiagArg
"""A value a message template's replacement field can be given."""


def template_fields(template: str) -> frozenset[str]:
    """Return the names of ``template``'s replacement fields.

    Every field must be a plain name, with no attribute, index, conversion or format spec.
    """
    fields: set[str] = set()
    for _, name, spec, conversion in string.Formatter().parse(template):
        if name is None:
            continue
        assert name.isidentifier(), f"not a plain field name: {name!r} in {template!r}"
        assert not spec and conversion is None, f"field {name!r} is formatted in {template!r}"
        fields.add(name)
    return frozenset(fields)


@dataclasses.dataclass(frozen=True)
class DiagKind:
    """One kind of diagnostic: a stable name, a default level and a message template.

    ``aliases`` are former names, which still find the kind by name.
    """

    name: str
    level: Level
    template: str
    aliases: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class MsgKind:
    """A template for a label or note, which has no name or level of its own."""

    template: str


class Msg:
    """A message: a template and the values of its replacement fields.

    ``args`` is a read-only copy of the arguments given, which must name exactly the
    template's fields.
    """

    __slots__ = ("args", "kind")

    kind: Final[DiagKind | MsgKind]
    args: Final[Mapping[str, DiagArgValue]]

    def __init__(self, kind: DiagKind | MsgKind, args: Mapping[str, DiagArgValue]) -> None:
        fields = template_fields(kind.template)
        assert args.keys() == fields, (
            f"arguments {sorted(args)} do not match the fields {sorted(fields)} of "
            f"{kind.template!r}"
        )
        self.kind = kind
        self.args = types.MappingProxyType(dict(args))

    def text(self) -> str:
        """Render the message, converting each argument to a string."""
        return self.kind.template.format_map(
            {name: _arg_str(value) for name, value in self.args.items()}
        )


def _arg_str(value: DiagArgValue) -> str:
    match value:
        case str():
            return value
        case int():
            return str(value)
        case _:
            return value.diag_str()


@dataclasses.dataclass(frozen=True)
class Label:
    """A secondary span, with the text shown beside its underline, if any."""

    span: src.SrcSpan
    msg: Optional[Msg]


@dataclasses.dataclass(frozen=True)
class Note:
    """A note following a diagnostic. A spanned note shows its own source excerpt."""

    msg: Msg
    span: Optional[src.SrcSpan]


@dataclasses.dataclass(frozen=True)
class Diag:
    """A diagnostic: a message of one kind, where it applies, and what explains it.

    ``span`` is the primary span, and ``primary_label`` the text shown beside its underline.
    ``level`` is the effective level, which is the kind's level unless an option promoted a
    warning to an error; ``promoted_by`` then names that option.
    """

    msg: Msg
    span: Optional[src.SrcSpan]
    level: Level
    primary_label: Optional[Msg] = None
    labels: tuple[Label, ...] = ()
    notes: tuple[Note, ...] = ()
    promoted_by: Optional[str] = None

    def __post_init__(self) -> None:
        assert isinstance(self.msg.kind, DiagKind), "a diagnostic's message needs a DiagKind"

    @classmethod
    def new(cls, kind: DiagKind, span: Optional[src.SrcSpan], /, **args: DiagArgValue) -> Self:
        """Return a diagnostic of ``kind`` at ``span``, at the kind's level."""
        return cls(Msg(kind, args), span, kind.level)

    @property
    def kind(self) -> DiagKind:
        assert isinstance(self.msg.kind, DiagKind)
        return self.msg.kind

    def with_primary_label(self, kind: MsgKind, /, **args: DiagArgValue) -> Self:
        """Return this diagnostic with ``kind``'s text beside the primary span's underline."""
        assert self.span is not None, "a spanless diagnostic has no primary label"
        return dataclasses.replace(self, primary_label=Msg(kind, args))

    def with_label(
        self, span: src.SrcSpan, kind: Optional[MsgKind] = None, /, **args: DiagArgValue
    ) -> Self:
        """Return this diagnostic with a secondary span, labelled with ``kind``'s text if
        given."""
        msg = None
        if kind is None:
            assert not args, "an unlabelled span takes no arguments"
        else:
            msg = Msg(kind, args)
        return dataclasses.replace(self, labels=(*self.labels, Label(span, msg)))

    def with_note(
        self, kind: MsgKind, span: Optional[src.SrcSpan] = None, /, **args: DiagArgValue
    ) -> Self:
        """Return this diagnostic with a note following it."""
        return dataclasses.replace(self, notes=(*self.notes, Note(Msg(kind, args), span)))

    def __str__(self) -> str:
        """The rendered message."""
        return self.msg.text()


type AnyDiag = Diag | errors.UserError
"""A diagnostic, as a catalogue ``Diag`` or as a ``UserError`` not yet moved to the catalogue."""


_KEY: Final = object()
"""Guards ``ReportProof``'s constructor, so only ``Diags`` creates proofs."""


class ReportProof:
    """Proof that an error diagnostic has been reported to a ``Diags``.

    Only ``Diags`` can create one. Code that needs to fail or recover without reporting
    anything itself requires a proof, so it cannot fail silently. ``diag`` is the reported
    error.
    """

    __slots__ = ("diag",)

    diag: Final[AnyDiag]

    def __init__(self, key: object, diag: AnyDiag) -> None:
        assert key is _KEY, "only Diags can create a ReportProof"
        self.diag = diag


class ReportedError(Exception):
    """Unwinds from a user error that has already been reported."""

    reported: Final[ReportProof]

    def __init__(self, reported: ReportProof) -> None:
        super().__init__("an error was reported")
        self.reported = reported


class CompilationError(Exception):
    """Raised when a compilation has errors.

    ``diags`` is every diagnostic the compilation reported, warnings included, in source
    order.
    """

    diags: Final[tuple[AnyDiag, ...]]

    def __init__(self, diags: Sequence[AnyDiag]) -> None:
        assert any(d.level == ERROR for d in diags), "a failed compilation has an error"
        super().__init__("compilation failed")
        self.diags = tuple(diags)

    @property
    def kinds(self) -> tuple[DiagKind, ...]:
        """The kind of each diagnostic, in order."""
        return tuple(d.kind for d in self.diags)


class InternalError(Exception):
    """A bug in the compiler that it detected itself, rather than a problem in the program.

    It is reported as an internal compiler error, not as a diagnostic.
    """


class Diags:
    """The diagnostics of one compilation, in emission order, without duplicates.

    A diagnostic duplicates an earlier one when it has the same kind, levels, messages and
    source locations. Source files are compared by resolved path, so diagnostics from
    separately loaded copies of a file are duplicates too.
    """

    _diags: Final[list[AnyDiag]]
    #: Each distinct diagnostic's key, mapped to its proof if it is an error.
    _seen: Final[dict[Hashable, Optional[ReportProof]]]
    _first_error: Optional[ReportProof]
    #: Each noted source file's position in file order, by resolved path.
    _file_ranks: Final[dict[pathlib.Path, int]]

    def __init__(self) -> None:
        self._diags = []
        self._seen = {}
        self._first_error = None
        self._file_ranks = {}

    def note_file(self, path: pathlib.Path) -> None:
        """Put ``path`` next in file order, unless it is already there.

        ``sorted`` orders diagnostics by their files' places in this order.
        """
        self._file_ranks.setdefault(path.resolve(), len(self._file_ranks))

    @overload
    def error(self, err: AnyDiag, /) -> ReportProof:
        pass

    @overload
    def error(
        self, kind: DiagKind, span: Optional[src.SrcSpan], /, **args: DiagArgValue
    ) -> ReportProof:
        pass

    def error(
        self,
        err: AnyDiag | DiagKind,
        span: Optional[src.SrcSpan] = None,
        /,
        **args: DiagArgValue,
    ) -> ReportProof:
        """Record an error unless it duplicates an earlier diagnostic.

        The error is a diagnostic, or is built from a kind, a primary span and the kind's
        message arguments, as ``Diag.new`` builds one. Returns its proof, or the proof of the
        earlier diagnostic it duplicates.
        """
        err = _built(err, span, args)
        assert err.level == ERROR, f"not an error: {err!r}"
        reported = self._record(err)
        assert reported is not None
        return reported

    @overload
    def raise_error(self, err: AnyDiag, /) -> NoReturn:
        pass

    @overload
    def raise_error(
        self, kind: DiagKind, span: Optional[src.SrcSpan], /, **args: DiagArgValue
    ) -> NoReturn:
        pass

    def raise_error(
        self,
        err: AnyDiag | DiagKind,
        span: Optional[src.SrcSpan] = None,
        /,
        **args: DiagArgValue,
    ) -> NoReturn:
        """Record an error as ``error`` does, then raise ``ReportedError`` with its proof."""
        raise ReportedError(self.error(_built(err, span, args)))

    @overload
    def warn(self, err: AnyDiag, /) -> None:
        pass

    @overload
    def warn(self, kind: DiagKind, span: Optional[src.SrcSpan], /, **args: DiagArgValue) -> None:
        pass

    def warn(
        self,
        err: AnyDiag | DiagKind,
        span: Optional[src.SrcSpan] = None,
        /,
        **args: DiagArgValue,
    ) -> None:
        """Record a warning, given as ``error`` takes an error, unless it duplicates an earlier
        diagnostic."""
        err = _built(err, span, args)
        assert err.level == WARNING, f"not a warning: {err!r}"
        self._record(err)

    def _record(self, err: AnyDiag) -> Optional[ReportProof]:
        """Record ``err`` unless it duplicates an earlier diagnostic, returning the proof of
        ``err`` or of the earlier diagnostic if it is an error."""
        key = _key(err)
        if key in self._seen:
            return self._seen[key]
        reported = None
        if err.level >= ERROR:
            reported = ReportProof(_KEY, err)
            if self._first_error is None:
                self._first_error = reported
        self._seen[key] = reported
        self._diags.append(err)
        return reported

    def all(self) -> tuple[AnyDiag, ...]:
        """Every distinct diagnostic, in emission order."""
        return tuple(self._diags)

    def sorted(self) -> tuple[AnyDiag, ...]:
        """Every distinct diagnostic, in source order.

        Diagnostics are ordered by their primary span's file in file order, then by its
        start, and otherwise keep their emission order. Files never noted follow every
        noted file, ordered by path, and a diagnostic without a span follows every
        diagnostic with one.
        """
        return tuple(sorted(self._diags, key=self._source_order_key))

    def _source_order_key(self, err: AnyDiag) -> tuple[int, int, str, int]:
        span = err.span
        if span is None:
            return (1, 0, "", 0)
        path = span.file.path.resolve()
        rank = self._file_ranks.get(path, len(self._file_ranks))
        return (0, rank, str(path), span.start)

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


def _built(
    err: AnyDiag | DiagKind, span: Optional[src.SrcSpan], args: Mapping[str, DiagArgValue]
) -> AnyDiag:
    if isinstance(err, DiagKind):
        return Diag.new(err, span, **args)
    assert span is None and not args, "a built diagnostic takes no other arguments"
    return err


def _key(err: AnyDiag) -> Hashable:
    match err:
        case Diag():
            return (
                err.kind,
                err.level,
                err.msg.text(),
                _location(err.span),
                _opt_text(err.primary_label),
                *((_location(label.span), _opt_text(label.msg)) for label in err.labels),
                *((note.msg.text(), _location(note.span)) for note in err.notes),
            )
        case _:
            return (type(err), *(_message_key(m) for m in (err.message, *err.extra)))


def _message_key(message: errors.Message) -> Hashable:
    return (message.level, message.message, _location(message.span))


def _opt_text(msg: Optional[Msg]) -> Optional[str]:
    if msg is None:
        return None
    return msg.text()


def _location(span: Optional[src.SrcSpan]) -> Hashable:
    if span is None:
        return None
    return (span.file.path.resolve(), span.start_line, span.start_col, span.end_line, span.end_col)
