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
    # ir_module imports this module, so it can only be imported here for
    # type annotations.
    from leech import ir_module


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


type CoercionKind = NeverDiverge | PtrMutRelax | IntExt | Invalid
"""Every coercion type checking can record for lowering."""

type BraceExprTyp = typs.ArrayTyp | typs.StructTyp
"""The resolved type of an array or struct brace literal."""


class TypCheckResults:
    """Lowering facts for one checked body, keyed by AST-node identity."""

    resolutions: Final[resolve.Resolutions]
    _folded_int_lits: Final[dict[ast.IntLit | ast.UnaryOpExpr, tuple[typs.IntTyp, int]]]
    _coercions: Final[dict[ast.Ast, Optional[CoercionKind]]]
    _applied_fns: Final[dict[ast.VarExpr, ir_module.AppliedFn]]
    _brace_expr_typs: Final[dict[ast.BraceExpr, BraceExprTyp]]
    _struct_field_indices: Final[dict[ast.FieldAccessExpr | ast.StructFieldExpr, int]]
    _let_declared_typs: Final[dict[ast.LetStmt, typs.Typ]]
    _match_scrutinee_typs: Final[dict[ast.MatchExpr, typs.Typ]]
    _match_plans: Final[dict[ast.MatchExpr, patterns.MatchPlan]]
    _local_typs: Final[dict[resolve.LocalDecl, typs.PtrTyp]]
    _expr_typs: Final[dict[ast.Expr, typs.Typ]]
    _place_typs: Final[dict[ast.Expr, typs.PtrTyp]]
    #: False during a speculative comptime-argument probe, when setters for
    #: context-dependent facts must discard their writes.
    _recording: bool

    def __init__(self) -> None:
        self.resolutions = resolve.Resolutions()
        self._folded_int_lits = {}
        self._coercions = {}
        self._applied_fns = {}
        self._brace_expr_typs = {}
        self._struct_field_indices = {}
        self._let_declared_typs = {}
        self._match_scrutinee_typs = {}
        self._match_plans = {}
        self._local_typs = {}
        self._expr_typs = {}
        self._place_typs = {}
        self._recording = True

    def folded_int_lit(
        self, node: ast.IntLit | ast.UnaryOpExpr
    ) -> Optional[tuple[typs.IntTyp, int]]:
        """Return the checked type and folded value for an integer literal, if recorded.

        Keyed by the ``IntLit`` itself, or by the ``UnaryOpExpr`` when a
        unary minus folded into it.
        """
        return self._folded_int_lits.get(node)

    def _set_folded_int_lit(
        self, node: ast.IntLit | ast.UnaryOpExpr, typ: typs.IntTyp, value: int
    ) -> None:
        self._set_fact(self._folded_int_lits, node, (typ, value))

    def coercion(self, node: ast.Ast) -> Optional[CoercionKind]:
        """Return ``node``'s coercion, or ``None`` when its type already matched."""
        return self._coercions[node]

    def _set_coercion(self, node: ast.Ast, coercion: Optional[CoercionKind]) -> None:
        self._set_fact(self._coercions, node, coercion)

    def applied_fn(self, node: ast.VarExpr) -> Optional[ir_module.AppliedFn]:
        """Return the function application recorded for ``node``, if it names one."""
        return self._applied_fns.get(node)

    def _set_applied_fn(self, node: ast.VarExpr, applied: ir_module.AppliedFn) -> None:
        self._set_fact(self._applied_fns, node, applied)

    def brace_expr_typ(self, node: ast.BraceExpr) -> BraceExprTyp:
        """Return ``node``'s resolved (possibly still abstract) type.

        A ``StructTyp`` or ``ArrayTyp`` depending on which kind of literal
        ``node`` turned out to be; the result must be matched to tell which.
        """
        return self._brace_expr_typs[node]

    def _set_brace_expr_typ(self, node: ast.BraceExpr, typ: BraceExprTyp) -> None:
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

    def match_plan(self, node: ast.MatchExpr) -> patterns.MatchPlan:
        """Return ``node``'s recorded match plan."""
        return self._match_plans[node]

    def _set_match_plan(self, node: ast.MatchExpr, plan: patterns.MatchPlan) -> None:
        self._set_fact(self._match_plans, node, plan)

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
