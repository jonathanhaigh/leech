# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Maranget-style pattern usefulness and exhaustiveness checking.

This is a pure leaf module: it imports neither ``leech.ast`` nor
``leech.typs``, so it can be unit-tested in isolation. A pattern matrix is
one row per match arm and one column per scrutinee position.

A constructor carries one constructor space per sub-field, so the
recursion threads a space per column: specialising on a constructor
replaces the head space with the constructor's field spaces, and
defaulting drops it. The public entry points describe a single scrutinee
column, since a match has exactly one scrutinee; extra columns appear only
inside the recursion, from constructor payloads.
"""

import dataclasses
from collections.abc import Sequence

from leech import asserts


@dataclasses.dataclass(frozen=True)
class Constructor:
    """Base class for the concrete constructor kinds.

    ``field_spaces`` holds the constructor space of each sub-field a value
    of this constructor carries, in field order. It is excluded from
    equality, so constructor identity stays on the case itself.
    """

    field_spaces: tuple[ConstructorSpace, ...] = dataclasses.field(
        default=(), compare=False, kw_only=True
    )

    @property
    def arity(self) -> int:
        """The number of sub-fields a value of this constructor carries."""
        return len(self.field_spaces)


@dataclasses.dataclass(frozen=True)
class VariantConstructor(Constructor):
    """An enum or union variant, identified by its integer discriminant.

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
    has an empty, closed space; a type uninhabited only through its
    payloads keeps a non-empty one, so a wildcard over it still counts as
    reachable.
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


@dataclasses.dataclass(frozen=True)
class MatchPlan:
    """How to lower one match, and what its arms cover."""

    reachable_arms: tuple[int, ...]
    missing: tuple[Witness, ...]


type Matrix = Sequence[tuple[PatternKind, ...]]
"""A pattern matrix: one row (a pattern per column) per match arm."""


def is_useful(matrix: Sequence[PatternKind], row: PatternKind, space: ConstructorSpace) -> bool:
    """Return whether ``row`` matches a value no earlier arm matches.

    ``matrix`` holds one translated pattern per preceding match arm, in
    arm order; ``space`` is the scrutinee type's constructor space.
    """
    return _is_useful(tuple((pattern,) for pattern in matrix), (row,), (space,))


def missing_patterns(matrix: Sequence[PatternKind], space: ConstructorSpace) -> list[Witness]:
    """Return one witness per uncovered value of a non-exhaustive matrix."""
    witnesses = _missing(tuple((pattern,) for pattern in matrix), (space,))
    return [Witness(w[0]) for w in witnesses]


def build_match_plan(rows: Sequence[PatternKind], space: ConstructorSpace) -> MatchPlan:
    """Analyse one match's arm matrix into everything lowering needs.

    ``reachable_arms`` holds the indices of the arms some value reaches,
    in arm order. ``missing`` is the witness patterns a non-exhaustive
    matrix leaves uncovered.

    Lowering tests the reachable arms in order and enters the last one
    unconditionally, which this asserts is sound: arms after the last
    reachable one are covered by earlier arms, so arms ``0..last`` cover
    everything an exhaustive matrix covers, and therefore arm ``last``
    covers whatever arms ``0..last-1`` leave.
    """
    rows = tuple(rows)
    reachable_arms = tuple(i for i, row in enumerate(rows) if is_useful(rows[:i], row, space))
    missing = tuple(missing_patterns(rows, space))

    if not missing and reachable_arms:
        last = reachable_arms[-1]
        covers = _is_wildcard_row(rows[last]) or _covers_remaining(
            rows[last], missing_patterns(rows[:last], space), space
        )
        assert covers, "the last reachable arm of an exhaustive matrix must cover the remainder"

    return MatchPlan(reachable_arms, missing)


def _is_wildcard_row(row: PatternKind) -> bool:
    """Return whether ``row`` matches every value of the scrutinee."""
    match row:
        case WildcardPattern():
            return True
        case ConstructorPattern():
            return False
        case OrPattern(alternatives):
            return any(isinstance(alternative, WildcardPattern) for alternative in alternatives)


def _covers_remaining(
    row: PatternKind, remaining: Sequence[Witness], space: ConstructorSpace
) -> bool:
    """Return whether ``row`` matches every witness earlier arms miss."""
    return all(not is_useful([row], witness.pattern, space) for witness in remaining)


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


def _is_complete(heads: set[ConstructorKind], space: ConstructorSpace) -> bool:
    """Return whether ``heads`` covers every constructor in ``space``.

    An open space has infinitely many values, so a finite set of
    constructors never covers it.
    """
    if space.is_open:
        return False
    return set(space.constructors).issubset(heads)


def _is_useful(
    matrix: Matrix, row: tuple[PatternKind, ...], spaces: tuple[ConstructorSpace, ...]
) -> bool:
    asserts.assert_eq(len(row), len(spaces))
    if not row:
        return not matrix
    head, *rest = row
    head_space, *rest_spaces = spaces
    match head:
        case WildcardPattern():
            if _is_complete(_head_constructors(matrix), head_space):
                return any(
                    _is_useful(
                        specialize(constructor, matrix),
                        ((WildcardPattern(),) * constructor.arity) + tuple(rest),
                        constructor.field_spaces + tuple(rest_spaces),
                    )
                    for constructor in head_space.constructors
                )
            return _is_useful(default(matrix), tuple(rest), tuple(rest_spaces))
        case OrPattern(alternatives):
            return any(
                _is_useful(matrix, (alternative, *rest), spaces) for alternative in alternatives
            )
        case ConstructorPattern(constructor, subpatterns):
            return _is_useful(
                specialize(constructor, matrix),
                (*subpatterns, *rest),
                constructor.field_spaces + tuple(rest_spaces),
            )


def _missing(matrix: Matrix, spaces: tuple[ConstructorSpace, ...]) -> list[tuple[PatternKind, ...]]:
    if not spaces:
        return [] if matrix else [()]
    head_space, *rest_spaces = spaces
    witnesses: list[tuple[PatternKind, ...]] = []
    for constructor in head_space.constructors:
        sub_spaces = constructor.field_spaces + tuple(rest_spaces)
        for sub_witness in _missing(specialize(constructor, matrix), sub_spaces):
            witnesses.append(
                (
                    ConstructorPattern(constructor, tuple(sub_witness[: constructor.arity])),
                    *sub_witness[constructor.arity :],
                )
            )
    if head_space.is_open:
        for sub_witness in _missing(default(matrix), tuple(rest_spaces)):
            witnesses.append((WildcardPattern(), *sub_witness))
    return witnesses


def _render_pattern(pattern: PatternKind) -> str:
    match pattern:
        case WildcardPattern():
            return "_"
        case ConstructorPattern(constructor, subpatterns):
            rendered = _render_constructor(constructor)
            if not subpatterns:
                return rendered
            payload = ", ".join(_render_pattern(subpattern) for subpattern in subpatterns)
            return f"{rendered}({payload})"
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
