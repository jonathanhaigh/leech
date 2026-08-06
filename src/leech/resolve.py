# SPDX-FileCopyrightText: 2026 Jonathan Haigh
#
# SPDX-License-Identifier: MPL-2.0

"""Name-resolution facts shared between type checking and CFG lowering.

TypCheck is the sole walker of :class:`~leech.ir_env.Env` for body-level
code; it records what each reference resolves to here, once, so CfgBuilder
never needs to re-resolve it.
"""

import enum
from typing import TYPE_CHECKING, Final, Optional

from leech import ast, ir_values

if TYPE_CHECKING:
    # Runtime import is local because ir_module imports typcheck, which
    # imports this module.
    from leech import ir_module

type LocalDecl = ast.Param | ast.Receiver | ast.LetStmt
"""A local binding's declaration-site AST node - its stable cross-phase identity."""

type VarTarget = (
    LocalDecl
    | ir_module.GenericFn
    | ir_module.ModVar
    | ir_module.FnSpec  # Fn | FnDecl | FnInstance | GenericBuiltinFn
    | ir_values.ComptimeEnum
)
"""What a ``VarExpr`` resolves to: a local binding, or a shared module item."""

type Callee = ir_module.Fn | ir_module.FnInstance
"""A callable a dot-call's member lookup found."""


class CalleeResolution(enum.Enum):
    """:meth:`Resolutions.callee`'s two non-callable outcomes."""

    # TODO: Python 3.15 adds a `sentinel()` builtin that gives each
    # sentinel its own precisely-typeable singleton type - once leech
    # updates to 3.15, reconsider replacing this enum with two such
    # sentinels instead. Not available on the 3.14 this project targets now.

    UNRESOLVED = enum.auto()
    """TypCheck never attempted this lookup: the receiver was still a
    ``TypParamTyp`` (trait-bound dispatch via ``_resolve_bound_method``)."""

    FIELD_ACCESS = enum.auto()
    """Confirmed: no callable member of that name; fall back to plain field access."""


class Resolutions:
    """Name-resolution facts for one checked body, keyed by AST-node identity."""

    _vars: Final[dict[ast.VarExpr, VarTarget]]
    _callees: Final[dict[ast.CallExpr, Callee | CalleeResolution]]
    _loop_targets: Final[dict[ast.BreakStmt | ast.ContinueStmt, ast.WhileExpr]]

    def __init__(self) -> None:
        self._vars = {}
        self._callees = {}
        self._loop_targets = {}

    def var(self, node: ast.VarExpr) -> VarTarget:
        """Return what ``node`` resolves to."""
        return self._vars[node]

    def set_var(self, node: ast.VarExpr, target: VarTarget) -> None:
        """Record what ``node`` resolves to."""
        self._vars[node] = target

    def callee(self, node: ast.CallExpr) -> Callee | CalleeResolution:
        """Return what a dot-call's member lookup resolved to for ``node``."""
        return self._callees.get(node, CalleeResolution.UNRESOLVED)

    def set_callee(self, node: ast.CallExpr, target: Optional[Callee]) -> None:
        """Record ``node``'s member-lookup result; ``None`` means confirmed field access."""
        self._callees[node] = CalleeResolution.FIELD_ACCESS if target is None else target

    def loop_target(self, node: ast.BreakStmt | ast.ContinueStmt) -> ast.WhileExpr:
        """Return the ``while`` loop ``node`` targets."""
        return self._loop_targets[node]

    def set_loop_target(
        self, node: ast.BreakStmt | ast.ContinueStmt, target: ast.WhileExpr
    ) -> None:
        """Record the ``while`` loop ``node`` targets."""
        self._loop_targets[node] = target
