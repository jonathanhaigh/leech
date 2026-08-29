# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Maranget-style pattern usefulness and exhaustiveness checking.

This is a pure leaf module: it imports neither ``leech.ast`` nor
``leech.typs``, so it can be unit-tested in isolation. A pattern matrix is
one row per match arm and one column per scrutinee position.

Every constructor has arity zero, so every matrix is one column wide and one
constructor space describes the whole scrutinee. ``specialize`` and
``default`` operate over sub-patterns, but ``is_useful`` and
``missing_patterns`` assume that single-column, single-space shape;
supporting constructors with arity (struct patterns, tuples, tagged-union
payloads) would additionally require threading a constructor space per
column through the recursion.
"""

import dataclasses
from collections.abc import Sequence
from typing import ClassVar


@dataclasses.dataclass(frozen=True)
class Constructor:
    """Base class for the concrete constructor kinds.

    ``arity`` is the number of sub-fields a value of this constructor
    carries. Every constructor has arity zero, since no pattern
    destructures yet.
    """

    arity: ClassVar[int] = 0


@dataclasses.dataclass(frozen=True)
class VariantConstructor(Constructor):
    """An enum variant, identified by its integer discriminant.

    Two variants with the same discriminant are the same constructor even
    when their spelled names differ, which is what makes
    ``enum Alias { A = 1, B = 1 }`` deduplicate: ``A`` and ``B`` are one
    case at runtime.
    """

    discriminant: int
    display_name: str = dataclasses.field(compare=False)


@dataclasses.dataclass(frozen=True)
class BoolConstructor(Constructor):
    """A boolean literal value."""

    value: bool


@dataclasses.dataclass(frozen=True)
class IntConstructor(Constructor):
    """An integer literal value."""

    value: int


type ConstructorKind = VariantConstructor | BoolConstructor | IntConstructor
"""A concrete constructor: an enum variant, boolean, or integer case."""


@dataclasses.dataclass(frozen=True)
class Pattern:
    """Base class for a match pattern."""


@dataclasses.dataclass(frozen=True)
class WildcardPattern(Pattern):
    """The ``_`` pattern, matching any value."""


@dataclasses.dataclass(frozen=True)
class ConstructorPattern(Pattern):
    """A constructor pattern: a specific case applied to sub-patterns."""

    constructor: ConstructorKind
    subpatterns: tuple[PatternKind, ...]


@dataclasses.dataclass(frozen=True)
class OrPattern(Pattern):
    """Alternatives joined by ``|``; the row matches any one of them."""

    alternatives: tuple[PatternKind, ...]


type PatternKind = WildcardPattern | ConstructorPattern | OrPattern
"""A concrete pattern: a wildcard, constructor, or or-pattern."""


@dataclasses.dataclass(frozen=True)
class ConstructorSpace:
    """The complete constructor set for a scrutinee type, plus openness.

    A closed space names every constructor (an enum or ``bool``); an open
    one does not (integers, and any type no constructor pattern reaches
    yet), so it can only be exhausted by a wildcard. An uninhabited type
    has an empty, closed space.
    """

    constructors: tuple[ConstructorKind, ...]
    is_open: bool

    @classmethod
    def from_constructors(
        cls, constructors: Sequence[ConstructorKind], is_open: bool
    ) -> ConstructorSpace:
        """Return a space, deduplicating constructors by equality.

        Order is preserved and the first constructor wins, so a witness is
        named by the first variant declared with its discriminant.
        """
        seen: set[ConstructorKind] = set()
        deduped: list[ConstructorKind] = []
        for constructor in constructors:
            if constructor not in seen:
                seen.add(constructor)
                deduped.append(constructor)
        return cls(tuple(deduped), is_open)


@dataclasses.dataclass(frozen=True)
class Witness:
    """A pattern matching exactly the values a matrix leaves uncovered."""

    pattern: PatternKind

    def render(self) -> str:
        """Render this witness as source-like pattern text."""
        return _render_pattern(self.pattern)


type Matrix = Sequence[tuple[PatternKind, ...]]
"""A pattern matrix: one row (a pattern per column) per match arm."""


def is_useful(matrix: Sequence[PatternKind], row: PatternKind, space: ConstructorSpace) -> bool:
    """Return whether ``row`` matches a value no earlier arm matches.

    ``matrix`` holds one translated pattern per preceding match arm, in
    arm order; ``space`` is the scrutinee type's constructor space.
    """
    return _is_useful(tuple((pattern,) for pattern in matrix), (row,), space)


def missing_patterns(matrix: Sequence[PatternKind], space: ConstructorSpace) -> list[Witness]:
    """Return one witness per uncovered value of a non-exhaustive matrix."""
    witnesses = _missing(tuple((pattern,) for pattern in matrix), space, 1)
    return [Witness(w[0]) for w in witnesses]


def specialize(constructor: ConstructorKind, matrix: Matrix) -> list[tuple[PatternKind, ...]]:
    """Return the rows of ``matrix`` matching ``constructor``, with sub-patterns expanded.

    Each surviving row contributes the constructor's own sub-patterns (a
    wildcard per sub-field when the row's head was a wildcard) followed by
    the row's remaining columns.
    """
    result: list[tuple[PatternKind, ...]] = []
    for row in matrix:
        result.extend(_specialize_row(constructor, row))
    return result


def default(matrix: Matrix) -> list[tuple[PatternKind, ...]]:
    """Return the rows of ``matrix`` whose head is a wildcard, head removed."""
    result: list[tuple[PatternKind, ...]] = []
    for row in matrix:
        result.extend(_default_row(row))
    return result


def _specialize_row(
    constructor: ConstructorKind, row: tuple[PatternKind, ...]
) -> list[tuple[PatternKind, ...]]:
    head, *rest = row
    match head:
        case WildcardPattern():
            return [((WildcardPattern(),) * constructor.arity) + tuple(rest)]
        case OrPattern(alternatives):
            result: list[tuple[PatternKind, ...]] = []
            for alternative in alternatives:
                result.extend(_specialize_row(constructor, (alternative, *rest)))
            return result
        case ConstructorPattern(other, subpatterns):
            if other == constructor:
                return [(*subpatterns, *rest)]
            return []


def _default_row(row: tuple[PatternKind, ...]) -> list[tuple[PatternKind, ...]]:
    head, *rest = row
    match head:
        case WildcardPattern():
            return [tuple(rest)]
        case OrPattern(alternatives):
            result: list[tuple[PatternKind, ...]] = []
            for alternative in alternatives:
                result.extend(_default_row((alternative, *rest)))
            return result
        case ConstructorPattern():
            return []


def _head_constructors(matrix: Matrix) -> set[ConstructorKind]:
    """Return the constructors at the head of any row, through nested alternatives."""
    result: set[ConstructorKind] = set()
    for row in matrix:
        if row:
            _add_head_constructors(row[0], result)
    return result


def _add_head_constructors(pattern: PatternKind, result: set[ConstructorKind]) -> None:
    if isinstance(pattern, ConstructorPattern):
        result.add(pattern.constructor)
    elif isinstance(pattern, OrPattern):
        for alternative in pattern.alternatives:
            _add_head_constructors(alternative, result)


def _has_wildcard_head(matrix: Matrix) -> bool:
    """Return whether any row matches every value in the first column."""
    return any(row and _is_wildcard_head(row[0]) for row in matrix)


def _is_wildcard_head(pattern: PatternKind) -> bool:
    if isinstance(pattern, WildcardPattern):
        return True
    return isinstance(pattern, OrPattern) and any(
        _is_wildcard_head(alternative) for alternative in pattern.alternatives
    )


def _is_complete(heads: set[ConstructorKind], space: ConstructorSpace) -> bool:
    """Return whether ``heads`` covers every constructor in ``space``.

    An open space has infinitely many values, so a finite set of
    constructors never covers it.
    """
    if space.is_open:
        return False
    return set(space.constructors).issubset(heads)


def _is_useful(matrix: Matrix, row: tuple[PatternKind, ...], space: ConstructorSpace) -> bool:
    if not row:
        return not matrix
    head, *rest = row
    match head:
        case WildcardPattern():
            if _has_wildcard_head(matrix):
                # Only correct at arity 0: `rest` is empty, so this call bottoms
                # out before `space` is read.
                return _is_useful(default(matrix), tuple(rest), space)
            heads = _head_constructors(matrix)
            if not _is_complete(heads, space):
                return True
            # Each branch descends into `constructor`'s sub-columns, which would
            # need their own spaces at arity > 0; at arity 0 there are none.
            return any(
                _is_useful(
                    specialize(constructor, matrix),
                    ((WildcardPattern(),) * constructor.arity) + tuple(rest),
                    space,
                )
                for constructor in heads
            )
        case OrPattern(alternatives):
            return any(
                _is_useful(matrix, (alternative, *rest), space) for alternative in alternatives
            )
        case ConstructorPattern(constructor, subpatterns):
            # `subpatterns` are new columns; at arity 0 they are empty, so the
            # outer `space` is never consulted. Arity > 0 needs per-column spaces.
            return _is_useful(specialize(constructor, matrix), (*subpatterns, *rest), space)


def _missing(matrix: Matrix, space: ConstructorSpace, width: int) -> list[tuple[PatternKind, ...]]:
    if width == 0:
        return [] if matrix else [()]
    witnesses: list[tuple[PatternKind, ...]] = []
    for constructor in space.constructors:
        sub_width = width - 1 + constructor.arity
        # The sub-columns would need their own spaces at arity > 0; at arity 0
        # there are none, so the outer `space` still describes them.
        for sub_witness in _missing(specialize(constructor, matrix), space, sub_width):
            witnesses.append(
                (
                    ConstructorPattern(constructor, tuple(sub_witness[: constructor.arity])),
                    *sub_witness[constructor.arity :],
                )
            )
    if space.is_open:
        # `default` descends to the next column, which would need its own space
        # at arity > 0; at arity 0 it is always empty.
        for sub_witness in _missing(default(matrix), space, width - 1):
            witnesses.append((WildcardPattern(), *sub_witness))
    return witnesses


def _render_pattern(pattern: PatternKind) -> str:
    match pattern:
        case WildcardPattern():
            return "_"
        case ConstructorPattern(constructor):
            return _render_constructor(constructor)
        case OrPattern(alternatives):
            return " | ".join(_render_pattern(alternative) for alternative in alternatives)


def _render_constructor(constructor: ConstructorKind) -> str:
    match constructor:
        case VariantConstructor(display_name=name):
            return name
        case BoolConstructor(value):
            return "true" if value else "false"
        case IntConstructor(value):
            return str(value)
