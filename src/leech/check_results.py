# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Lowering facts recorded by type checking, keyed by AST-node identity.

This is the checker's interface to lowering, beside the name-resolution facts in
``resolve.py``.
"""

import dataclasses
from typing import TYPE_CHECKING, Final, Optional

from leech import asserts, ast, patterns, resolve, typs

if TYPE_CHECKING:
    # Runtime imports are local because ir_module imports this module.
    from leech import ir_module


def _pattern_constructor_candidates(
    pattern: patterns.PatternKind,
) -> tuple[Optional[patterns.ConstructorKind], ...]:
    """Return one candidate constructor per top-level pattern alternative.

    Wildcards and bindings have no constructor, so their entry is ``None``.
    """
    match pattern:
        case patterns.WildcardPattern():
            return (None,)
        case patterns.ConstructorPattern(constructor):
            return (constructor,)
        case patterns.OrPattern(alternatives):
            result: list[Optional[patterns.ConstructorKind]] = []
            for alternative in alternatives:
                if isinstance(alternative, patterns.ConstructorPattern):
                    result.append(alternative.constructor)
                else:
                    result.append(None)
            return tuple(result)


class Coercion:
    """A required conversion to the type an expression's context expects."""


@dataclasses.dataclass(frozen=True)
class NeverDiverge(Coercion):
    """Replace an unreachable ``never`` value with a placeholder of ``target``."""

    target: typs.Typ


@dataclasses.dataclass(frozen=True)
class PtrMutRelax(Coercion):
    """A ``*mut T`` value given where a ``*T`` is wanted.

    The two share a representation, so nothing needs to be emitted.
    """


@dataclasses.dataclass(frozen=True)
class IntExt(Coercion):
    """Zero/sign-extend an integer value to a wider integer type."""

    target: typs.IntTyp


@dataclasses.dataclass(frozen=True)
class Invalid(Coercion):
    """The value's type doesn't coerce to the target at all."""


class TypCheckResults:
    """Lowering facts for one checked body, keyed by AST-node identity."""

    resolutions: Final[resolve.Resolutions]
    _int_lit_typs: Final[dict[ast.IntLit, typs.IntTyp]]
    _coercions: Final[dict[ast.Ast, Optional[Coercion]]]
    _generic_calls: Final[dict[ast.CallExpr, tuple[ir_module.FnSymbol, tuple[typs.Typ, ...]]]]
    _generic_var_refs: Final[dict[ast.VarExpr, tuple[ir_module.FnSymbol, tuple[typs.Typ, ...]]]]
    _brace_expr_typs: Final[dict[ast.BraceExpr, typs.Typ]]
    _struct_field_indices: Final[dict[ast.FieldAccessExpr | ast.StructFieldExpr, int]]
    _let_declared_typs: Final[dict[ast.LetStmt, typs.Typ]]
    _match_scrutinee_typs: Final[dict[ast.MatchExpr, typs.Typ]]
    _match_arm_patterns: Final[dict[ast.MatchArm, patterns.PatternKind]]
    _local_typs: Final[dict[resolve.LocalDecl, typs.PtrTyp]]
    _expr_typs: Final[dict[ast.Expr, typs.Typ]]
    _place_typs: Final[dict[ast.Expr, typs.PtrTyp]]
    #: False during a speculative comptime-argument probe, when setters for
    #: context-dependent facts must discard their writes.
    _recording: bool

    def __init__(self) -> None:
        self.resolutions = resolve.Resolutions()
        self._int_lit_typs = {}
        self._coercions = {}
        self._generic_calls = {}
        self._generic_var_refs = {}
        self._brace_expr_typs = {}
        self._struct_field_indices = {}
        self._let_declared_typs = {}
        self._match_scrutinee_typs = {}
        self._match_arm_patterns = {}
        self._local_typs = {}
        self._expr_typs = {}
        self._place_typs = {}
        self._recording = True

    def int_lit_typ(self, node: ast.IntLit) -> typs.IntTyp:
        """Return the type chosen and overflow-checked for an integer literal."""
        return self._int_lit_typs[node]

    def _set_int_lit_typ(self, node: ast.IntLit, typ: typs.IntTyp) -> None:
        self._set_fact(self._int_lit_typs, node, typ)

    def coercion(self, node: ast.Ast) -> Optional[Coercion]:
        """Return ``node``'s coercion, or ``None`` when its type already matched."""
        return self._coercions[node]

    def _set_coercion(self, node: ast.Ast, coercion: Optional[Coercion]) -> None:
        self._set_fact(self._coercions, node, coercion)

    def generic_call(
        self, node: ast.CallExpr
    ) -> Optional[tuple[ir_module.FnSymbol, tuple[typs.Typ, ...]]]:
        """Return a generic call's resolved function and comptime arguments, if any."""
        return self._generic_calls.get(node)

    def _set_generic_call(
        self, node: ast.CallExpr, fn: ir_module.FnSymbol, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        self._generic_calls[node] = (fn, comptime_args)

    def generic_var_ref(
        self, node: ast.VarExpr
    ) -> Optional[tuple[ir_module.FnSymbol, tuple[typs.Typ, ...]]]:
        """Return an applied generic function reference and its arguments, if any."""
        return self._generic_var_refs.get(node)

    def _set_generic_var_ref(
        self, node: ast.VarExpr, fn: ir_module.FnSymbol, comptime_args: tuple[typs.Typ, ...]
    ) -> None:
        self._generic_var_refs[node] = (fn, comptime_args)

    def brace_expr_typ(self, node: ast.BraceExpr) -> typs.Typ:
        """Return ``node``'s resolved (possibly still abstract) type.

        A ``StructTyp`` or ``ArrayTyp`` depending on which kind of literal
        ``node`` turned out to be - callers match on the result to tell
        which.
        """
        return self._brace_expr_typs[node]

    def _set_brace_expr_typ(self, node: ast.BraceExpr, typ: typs.Typ) -> None:
        self._set_fact(self._brace_expr_typs, node, typ)

    def struct_field_index(self, node: ast.FieldAccessExpr | ast.StructFieldExpr) -> int:
        """Return the declaration-order index resolved for a struct field expression."""
        return self._struct_field_indices[node]

    def _set_struct_field_index(
        self, node: ast.FieldAccessExpr | ast.StructFieldExpr, index: int
    ) -> None:
        self._set_fact(self._struct_field_indices, node, index)

    def let_declared_typ(self, node: ast.LetStmt) -> Optional[typs.Typ]:
        """Return ``node``'s declared type, if any."""
        return self._let_declared_typs.get(node)

    def _set_let_declared_typ(self, node: ast.LetStmt, typ: typs.Typ) -> None:
        self._set_fact(self._let_declared_typs, node, typ)

    def match_scrutinee_typ(self, node: ast.MatchExpr) -> typs.Typ:
        """Return ``node``'s checked scrutinee type."""
        return self._match_scrutinee_typs[node]

    def _set_match_scrutinee_typ(self, node: ast.MatchExpr, typ: typs.Typ) -> None:
        self._set_fact(self._match_scrutinee_typs, node, typ)

    def match_arm_pattern(self, node: ast.MatchArm) -> patterns.PatternKind:
        """Return ``node``'s checked, translated pattern."""
        return self._match_arm_patterns[node]

    def _set_match_arm_pattern(self, node: ast.MatchArm, pattern: patterns.PatternKind) -> None:
        self._set_fact(self._match_arm_patterns, node, pattern)

    def match_arm_constructors(
        self, node: ast.MatchArm
    ) -> tuple[Optional[patterns.ConstructorKind], ...]:
        """Return one resolved constructor candidate per pattern alternative."""
        return _pattern_constructor_candidates(self._match_arm_patterns[node])

    def local_typ(self, decl: resolve.LocalDecl) -> typs.PtrTyp:
        """Return the bound type and mutability for a local declaration."""
        return self._local_typs[decl]

    def _set_local_typ(self, decl: resolve.LocalDecl, typ: typs.PtrTyp) -> None:
        self._local_typs[decl] = typ

    def expr_typ(self, node: ast.Expr) -> Optional[typs.Typ]:
        """Return ``node``'s checked expression type, if one was recorded."""
        return self._expr_typs.get(node)

    def _set_expr_typ(self, node: ast.Expr, typ: typs.Typ) -> None:
        self._set_fact(self._expr_typs, node, typ)

    def place_typ(self, node: ast.Expr) -> Optional[typs.PtrTyp]:
        """Return ``node``'s checked place type, if one was recorded."""
        return self._place_typs.get(node)

    def _set_place_typ(self, node: ast.Expr, typ: typs.PtrTyp) -> None:
        self._set_fact(self._place_typs, node, typ)

    def _set_fact[K, V](self, mapping: dict[K, V], node: K, value: V) -> None:
        if not self._recording:
            return
        missing = object()
        previous = mapping.get(node, missing)
        if previous is not missing:
            asserts.assert_eq(previous, value)
        mapping[node] = value
